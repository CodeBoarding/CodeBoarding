# Edge Grounding & Relabel Stability — Status and Next Steps

How incremental analysis decides which **relation edges** changed, why untouched
edges used to churn, what this branch fixed, and what is left.

> Companion to [incremental-cluster-grouping.md](incremental-cluster-grouping.md),
> which covers how component *structure* stays stable. This doc is about the
> *edges* between components.

---

## Background: three edge layers

A relation between two components carries method-to-method call edges:

- **`all_edges`** — for a statically-backed pair (`is_static=True`) these are the
  deterministic CFG cross-component calls. They are the structural truth.
- **`key_edges`** — the LLM's highlighted subset, kept only where it names a real
  CFG edge (grounded against `all_edges`).
- **runtime/config relations** (`is_static=False`) — a pair the CFG does **not**
  connect. Here there is no static edge to ground against, so the LLM's
  `key_edges` are promoted into `all_edges` as-is.

The incremental diff flags an edge as *changed* when the `(source, target)`
method pair appears on one side and not the other. It is *ungrounded* when
**neither endpoint method's content changed** — i.e. no code edit explains the
edge moving. Ungrounded churn is the thing a reader notices as "the diagram
changed even though I didn't touch that part."

## What this branch fixed

1. **Ground edges in the static CFG** (`ground_relation_edges`). For a
   statically-backed pair, `all_edges` = the CFG cross-component call set; the
   LLM contributes wording, never edges. An edge the LLM invents or spells
   differently each run can no longer appear/vanish/duplicate.
2. **Carry unchanged-method edges forward** (`_reconcile_unchanged_edges` in
   `preserve_unchanged_relations`). Within a pair, the fresh rebuild is trusted
   only for edges that touch a changed/added/deleted method; every edge between
   two byte-identical methods is taken from the baseline. A call lives inside its
   source method's body, so an unchanged source cannot have changed what it calls
   — a rebuilt add/drop there is re-attribution, not a code change.
3. **Carry the label forward on unchanged edges, not endpoints.** A pair is
   re-worded only when its own backing edges moved, not when either endpoint
   component was merely flagged changed.
4. **Drop statically-backed component self-loops** (`drop_internal_self_relations`).
   A `src_id == dst_id` relation is an intra-component call with no
   cross-component connection to draw at the current granularity. The CFG edge is
   untouched, so expanding the component re-materialises it as a real
   cross-component relation once its endpoints land in different children.
   Edgeless (runtime/config) self-relations are left alone.

## How to measure it

`CodeBoarding-evals` computes an `edge_grounding` drift metric: over every
relation at every scope, the symmetric difference of backing edges between base
and head, bucketed into `grounded` / `ungrounded` / `undecidable` by whether an
endpoint's content changed. The pair-check
`drift.backing_edges_changed_without_code_change` surfaces it.

**Measurement rule (learned the hard way): the baseline must be built with the
same engine as the head.** A self-loop present in a pre-fix baseline shows up as
a *deletion* against a fixed head, so the fix looks neutral or worse. Always
regenerate the full baseline with the engine under test before running the
incremental against it.

## Results (small variant, matched baseline)

Ungrounded backing-edge changes per test, old baseline → engine-matched baseline:

| test | old baseline | matched baseline |
|---|---|---|
| capability-retired (optional-dep removal) | 5 | **0** |
| whole-module-deleted | 0 | **0** |
| body-edit-no-call-change | 0 | **0** |
| referenced-symbol-deleted | 5 | 6 |

Benchmark: 25/28 criteria (3 `structural_operations` violations) →
**27/28 (1 violation)**. Three of four small tests reach **zero** ungrounded edge
churn once base and head share the engine.

## Residual: why `referenced-symbol-deleted` still churns

Deleting a widely-referenced symbol forces re-clustering, which shifts the
component-pairs edges land in. Its 6 residual ungrounded edges decompose into
three distinct mechanisms:

1. **`is_static=False` LLM escape-hatch (dominant, ~4/6).** For a pair the CFG
   does not connect, the LLM's method-edges enter `all_edges` ungrounded. When a
   deletion re-clusters, these land on *new* component-pairs, so the
   pair-keyed carry-forward has no baseline to reconcile against and lets them
   through.
2. **Base deficiency (~1/6).** The head surfaces a real CFG edge
   (`is_static=True`) that the base's clustering/LLM missed. The head is *more*
   correct; it is only "churn" because no code change explains it.
3. **Full-vs-incremental CFG re-resolution (~1/6).** A polymorphic call resolves
   to an extra subclass-override target in the incremental head that the full
   base did not list (e.g. `Group.get_command` alongside `MultiCommand.get_command`).
   Real and grounded, but a resolution difference between a full and an
   incremental run.

## Next steps, in priority order

1. **Method-keyed carry-forward for non-static edges** (closes mechanism 1).
   Today carry-forward keys on the component-pair `(src_id, dst_id)`; a
   clustering shift produces a new pair with no baseline, so the guard can't
   reconcile it. For `is_static=False` LLM method-edges, key the carry-forward on
   the **method-edge identity** instead: carry the baseline's edge forward
   whenever *both endpoints are unchanged methods*, regardless of which
   component-pair it currently lands in. Must **carry forward, never
   blanket-drop** — some runtime/dynamic-dispatch relations are legitimate.
2. **Canonicalise edge endpoints.** Ensure `(source, target)` endpoint strings
   are spelled identically across runs (full path + canonical qualified name) so
   the raw-string diff never flags an edge that is genuinely the same.
3. **Clustering determinism (deeper root).** Mechanisms 1 and 3 both trace to
   full-vs-incremental clustering/CFG-resolution differences. Making the
   incremental reproduce the full run's method→component assignment (and call
   resolution) for unchanged code would remove the pair-shifts at the source.
   See the `REGROUP_DRIFT_BUDGET` escape hatch in
   [incremental-cluster-grouping.md](incremental-cluster-grouping.md) — the same
   large-change reshuffle that reshapes structure is what shifts these pairs.
4. **Cost tie-in.** The over-broad "changed component" set that inflates edge
   churn also drives runtime: ~96% of incremental wall time is LLM relation
   regeneration across touched scopes. Narrowing the touched set (method-granular
   change attribution) improves stability *and* speed together.
