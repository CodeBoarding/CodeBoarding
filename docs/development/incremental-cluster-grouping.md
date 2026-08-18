# Incremental Cluster Grouping

How the incremental path decides component structure — grouping, additions,
removals — and why (or whether) the diagram stays stable when a lot changes.

> Scope: the **incremental** analysis path (`generate_analysis_incremental`),
> not full analysis. Structure here is **derived deterministically**; the LLM
> only names/describes components and discovers relations.

---

## TL;DR

- Component structure in incremental is derived by code, not asked of an LLM.
  The LLM only supplies wording and relations.
- Stability comes from **two stacked deterministic layers**:
  1. **Leaf clustering** — seeded warm-start Leiden that locks every vertex
     outside the change's 1-hop frontier, so unchanged code keeps its cluster.
  2. **Component grouping** (`anchored_grouping`) — each live leaf cluster keeps
     the component that previously owned most of its methods; new clusters are
     absorbed into the nearest existing component; a component holding nothing is
     dropped.
- On top of those, orchestration runs **restore passes** that pin unchanged
  methods, metadata, and whole subtrees back to the baseline so a small edit
  can't ripple into parts nothing touched.
- **A large change can still reshuffle the diagram significantly**, by one
  explicit mechanism: the `REGROUP_DRIFT_BUDGET = 0.10` escape hatch. When the
  carried-forward grouping scores more than 0.10 modularity below a from-scratch
  optimum, the whole component structure is re-derived and only identities are
  salvaged.

---

## Pipeline overview

Entry point: [`generate_analysis_incremental`](../../diagram_analysis/diagram_generator.py) (~line 1296).

```
remove_deleted_files                     # scrub dead-file refs first
  -> compute_changed_members             # per-method content-hash change signal
  -> compute_cluster_delta               # seeded warm-start Leiden (leaf level)
  -> _apply_incremental_scope_recursively
        -> plan_scope_update             # DERIVE add/remove/update ops (no LLM)
        -> IncrementalAgent.update_scope # materialize ops, patch file_methods
  -> _restore_unchanged_membership       # pin unchanged methods to baseline owner
  -> _restore_unchanged_subtrees         # freeze subtrees with no changed member
  -> _rescope_child_analyses             # reconcile children whose parent moved
  -> assert_scope_containment
  -> _restore_unchanged_metadata         # undo reworded names/descriptions
  -> prune_empty_components              # drop methodless components
  -> _generate_subcomponents             # re-expand genuinely new components
  -> generate_all_scope_relations        # regenerate edges for touched scopes
  -> finalize_and_save
```

Two guardrails fail loud instead of silently rebuilding (per `AGENTS.md` §5):

- No trustworthy cluster baseline → `IncrementalCacheMissingError`
  ([diagram_generator.py:1374](../../diagram_analysis/diagram_generator.py)).
- A scope loses every cluster but still holds live methods →
  `IncrementalClusteringError` ([scope_plan.py:145](../../diagram_analysis/scope_plan.py)).

---

## Layer 1 — Leaf clustering (seeded warm-start Leiden)

Files: [`cluster_delta.py`](../../diagram_analysis/cluster_delta.py),
[`leiden_utils.py`](../../static_analyzer/leiden_utils.py).

`find_partition_seeded` ([leiden_utils.py:66](../../static_analyzer/leiden_utils.py#L66))
runs Leiden through the `Optimiser` API with:

- `initial_membership` = the **prior run's partition** (warm start), and
- `is_membership_fixed` = a per-vertex lock mask that pins every vertex **outside
  the 1-hop frontier of the change**.

Consequence: a method whose neighborhood didn't change keeps its leaf-cluster
identity across runs. Only the affected frontier re-optimizes (and it can pull
existing nodes into a newly-formed cluster with the added nodes). Leiden is also
**seeded** (`optimiser.set_rng_seed(seed)`), so the result is deterministic given
the same graph — there is no run-to-run randomness at this level.

`compute_changed_members` produces the member-granular change signal from
per-method content hashes, so a body-only edit lights up only the clusters whose
own members changed, not every cluster sharing the file.

---

## Layer 2 — Component grouping (`anchored_grouping`)

Files: [`scope_plan.py`](../../diagram_analysis/scope_plan.py),
[`anchored_grouping` in cluster_helpers.py:504](../../static_analyzer/cluster_helpers.py#L504).

Full analysis picks component count and membership from scratch
(`supercluster_by_modularity_peak`: a resolution-tuned Leiden over the
inter-cluster meta-graph, N chosen at the modularity peak in `[low, high]`).
Modularity has a **degenerate solution landscape** — many partitions score
within noise of each other — so a two-line diff can flip which near-optimal
partition wins and reshuffle ownership. Deterministic, but **not continuous**.

Incremental needs continuity, so `anchored_grouping` repairs the previous
grouping instead of re-deriving one:

1. **Carry forward** — one group per surviving component, built from
   `previous_owner` (leaf cluster id → the component that owned most of its
   methods; see `previous_ownership`, [scope_plan.py:44](../../diagram_analysis/scope_plan.py#L44)).
   Ownership is anchored on **qualified method names, not stored cluster ids**,
   because leaf cluster integer ids renumber whenever code in the scope changes;
   qnames survive until the method itself is deleted.
2. **New subsystems** — a fresh-optimum community made **entirely of new
   clusters** and of size ≥ 2 is promoted to its own unowned group
   ([cluster_helpers.py:556](../../static_analyzer/cluster_helpers.py#L556)).
3. **Absorb** — every remaining new cluster is folded into the nearest existing
   group by call proximity, then directory affinity, smaller seed winning ties.
4. **Drift gate** — score the carried grouping vs a fresh optimum. If
   `fresh_modularity - carried_modularity > REGROUP_DRIFT_BUDGET (0.10)`,
   discard the carry-forward and **re-derive from scratch**, salvaging only
   identities via `_inherit_ids` (dominant method-mass keeps the id). The result
   is flagged `regrouped=True` so the caller can log "structure re-derived".

---

## Q2 — When is a component added / removed / updated?

`plan_scope_update` ([scope_plan.py:110](../../diagram_analysis/scope_plan.py#L110))
zips `anchored_grouping`'s groups with their inherited owners and emits one of:

| Outcome | Condition | Code |
|---|---|---|
| **NOOP** (no op emitted) | Group's owner holds *exactly* the same qualified cluster ids **and** the same method set, and the component wasn't edited | [scope_plan.py:203](../../diagram_analysis/scope_plan.py#L203) |
| **UPDATE_COMPONENT** | Group has an owner but its cluster set moved, its methods changed, or a member was edited | [scope_plan.py:210](../../diagram_analysis/scope_plan.py#L210) |
| **CREATE_COMPONENT** (add) | Group has **no** predecessor owner in this scope (a promoted new subsystem) | [scope_plan.py:218](../../diagram_analysis/scope_plan.py#L218) |
| **DELETE_COMPONENT** (remove) | A prior component is **not in `kept`** — it holds no cluster in the new grouping | [scope_plan.py:229](../../diagram_analysis/scope_plan.py#L229) |
| **DELETE all** | Every cluster in the scope is gone *and* no component still holds methods | [scope_plan.py:146](../../diagram_analysis/scope_plan.py#L146) |
| **raise** | Every cluster gone but a component still holds live methods (missed change) → `IncrementalClusteringError` | [scope_plan.py:145](../../diagram_analysis/scope_plan.py#L145) |

Two more removal paths run later in orchestration:

- **`prune_empty_components`** ([incremental_agent.py:675](../../agents/incremental_agent.py#L675))
  removes any component with no methods and no key-entities (except
  cluster-backed data-only components, which are protected).
- **`remove_deleted_files`** ([incremental_agent.py:641](../../agents/incremental_agent.py#L641))
  scrubs file-method groups and key-entities for files that no longer exist.

Key subtleties:

- **UPDATE replaces, never unions** the cluster set
  ([incremental_agent.py:216](../../agents/incremental_agent.py#L216)). The plan
  emits a component's *complete* new cluster set every time, so a cluster it no
  longer lists is genuinely gone. `_remove_reassigned_clusters` strips the
  referenced ids from every component first, so assigning the op's refs is the
  whole set.
- **A survivor holding exactly what it held gets no operation at all** — an
  operation is not free (it re-runs LLM relation analysis for the whole scope),
  so emitting one per survivor would relabel every edge in the tree on a
  one-line diff.
- A `DELETE_COMPONENT` whose component still has **live CFG methods** is turned
  into a refresh instead of a removal
  ([incremental_agent.py:123](../../agents/incremental_agent.py#L123)).

Depth expansion adds a second way to gain components: a newly-created component
below the depth cap is re-expanded into sub-components by `_generate_subcomponents`
(gated by the deterministic `should_expand_component` / `component_is_separable`
in [planner_agent.py](../../agents/planner_agent.py)).

---

## Q3 — Can a lot of changes change the diagram significantly? How?

Yes. In the steady state the anchoring keeps the diagram stable, but there are
**three mechanisms** by which large change sets move it, in increasing severity:

### 1. New subsystems get added
A fresh community made entirely of new leaf clusters (≥2) becomes its own new
component ([cluster_helpers.py:556](../../static_analyzer/cluster_helpers.py#L556)).
The rest of the diagram is untouched; the tree grows.

### 2. The warm-start frontier widens
The larger the change, the larger the 1-hop affected frontier that Leiden is
allowed to re-cluster at the leaf level. Enough churn in one region re-shapes
that region's leaf clusters even though the rest of the graph stays locked.

### 3. Drift-budget regroup (the big one)
`REGROUP_DRIFT_BUDGET = 0.10` ([cluster_helpers.py:457](../../static_analyzer/cluster_helpers.py#L457)).
When the carried grouping's modularity falls **more than 0.10 below** a
from-scratch optimum, `anchored_grouping` throws away the carry-forward and
returns a **fresh partition** (`regrouped=True`,
[cluster_helpers.py:565](../../static_analyzer/cluster_helpers.py#L565)):

- The top-level component **count** can shift anywhere in `[5, 8]` (root) or
  `[3, 8]` (sub-scope) — it's re-chosen at the modularity peak.
- Membership is re-partitioned wholesale; unchanged code can move components.
- `_inherit_ids` salvages identity by dominant method-mass, so most components
  keep their id and name — but two old components merged into one fresh group
  means one of them is **deleted**, and a fresh group with no dominant
  predecessor comes out **new/unnamed**.

This is the intended answer to "the code has genuinely moved on": below the
budget, identity is worth more than the last few points of coupling; above it,
the diagram no longer describes the code, so it's rebuilt.

### Damping that offsets all three
Even when structure moves, several passes pull unchanged parts back to baseline
so the *visible* churn tracks the *real* change:

- `_restore_unchanged_membership` — unchanged methods pinned to their baseline
  owner so a re-partition only moves what changed.
- `_restore_unchanged_subtrees` — a component with no changed member has its
  whole subtree frozen byte-for-byte.
- `_restore_unchanged_metadata` — names/descriptions the planner reworded are
  reverted for components that didn't change.
- `preserve_unchanged_relations` / baseline global relations — an edge between
  two unchanged components is carried over verbatim rather than reworded.

Net: the diagram changes roughly in proportion to the change **until** the drift
budget trips, at which point it can jump.

---

## Potential mistakes / risks

> **Preliminary — pending adversarial verification.** These are candidates from a
> direct read; a verification pass is in flight and this section will be updated
> with confirmed/refuted verdicts.

1. **Full-vs-incremental asymmetry for a large, call-isolated new module.**
   In full analysis a big but call-isolated module (one leaf cluster nothing
   calls) is promoted to its own component via the seed floor in
   `_seeds_from_partition`. In incremental, `new_subsystems` requires the fresh
   community to have **size ≥ 2** ([cluster_helpers.py:556](../../static_analyzer/cluster_helpers.py#L556)),
   so a large single-cluster newcomer is **absorbed** into an existing component
   instead of becoming its own — unless the drift budget happens to trip. Same
   codebase can produce different top-level structure depending on whether it was
   built full or incrementally.

2. **Sub-scope cluster-id renumbering weakens the NOOP fast-path.** The
   untouched check compares the group's new qualified cluster ids against the
   component's stored `source_cluster_ids`
   ([scope_plan.py:203](../../diagram_analysis/scope_plan.py#L203)). At root the
   ids are seeded/stable, but sub-scope leaf ids renumber every run (per
   `previous_ownership`'s own docstring), so the equality rarely holds and
   sub-scopes emit `UPDATE` — hence more relation regeneration — more often than
   root. Bounded by the restore/preserve passes at save time, but it's extra work
   and extra churn surface.

3. **Drift regroup vs. child subtrees.** When the drift budget trips and the
   parent structure is re-derived, `_inherit_ids` can hand a component id to a
   group that mostly holds *different* code than the id's baseline. Method-level
   moves are reconciled by `_rescope_child_analyses`, but a wholesale identity
   shift could leave a child `sub_analyses` scope describing a parent it no
   longer matches. Worth confirming the rescope/containment passes fully cover
   the `regrouped=True` case.

---

## Key files

| Concern | File |
|---|---|
| Orchestration, restore passes | `diagram_analysis/diagram_generator.py` |
| Derive add/remove/update ops | `diagram_analysis/scope_plan.py` |
| Materialize ops, patch methods | `agents/incremental_agent.py` |
| Anchored grouping + drift budget | `static_analyzer/cluster_helpers.py` |
| Seeded warm-start Leiden | `static_analyzer/leiden_utils.py`, `diagram_analysis/cluster_delta.py` |
| Depth-expansion gates | `agents/planner_agent.py` |
| Relation/edge preservation | `agents/relation_edges.py`, `agents/validation.py` |
