# Partitioning by qualified names: one tree, three decisions

**Status.** Design for the `feat/name-tree-*` stack. Supersedes the clustering half of
PR #539 and the deletion in #542; builds on the merged naming fixes #543, #544, #545.
Evidence is summarised in §7 and lives in the research artifact
"One Tree, Three Decisions" (2 Sep 2026).

## 1. The problem in one paragraph

Components today come from the call graph twice over: Leiden communities at every depth,
a modularity peak to group them, seeded Leiden plus ownership anchoring on incremental
runs, and a modularity gate to decide expansion. Measured against the only independent
ground truth we have (eShop's published architecture), the call graph provably cannot
generate the maintainers' boxes: 204 connected components against 10 boxes, and no
resolution keeps one box both whole and separated. The shipped pipeline scores below the
all-in-one floor on two of four held-out rulers. What *does* reach the maintainers'
drawings is what files are called: the trie of qualified-name prefixes, which is the
directory tree in every language today, plus the words in the identifiers. PR #539 showed
that on the C# rulers; it broke on mono-repos, layered repos and path-derived languages
because its decisions were made at the wrong altitude (one gate for the whole repo, one
directory level, one sink, Leiden below the root). This design makes the same decisions
per scope, from the same names, writes them down once, and replays them everywhere.

## 2. The contract

- **Input.** The call graph's node keys (`CallGraph.nodes`, split on `graph.delimiter`)
  and, per node, the file it was declared in. The file path is an opaque unit identity and
  is never parsed: the partition reads no filesystem and no path segment. Only what static
  analysis emitted is partitioned; tests, docs and tooling that survive the ignore file are
  ordinary names, nothing else is special-cased.
- **Output.** A `TreeSpec`: per scope, an ordered list of `ComponentRule`s (the prefixes
  of the trie a component owns, the words it owns, a last-resort prefix, and the candidates
  it was grouped from), or no rules and a reason, meaning the scope is a leaf. Persisted in
  `analysis.json` metadata as `tree_spec`.
- **The one function.** `replay(units, scope_rules) -> Partition` assigns every unit of a
  scope to exactly one rule by: the longest matching prefix, then a head-noun-weighted vote
  over the words the rules own, then the longest matching fallback prefix. What no rule
  claims is *unplaced*: drawn in a reserved bucket, reported, never invented into a
  neighbour. Replay is used at draft time, on every incremental run, and on every partial
  expansion, so the three paths cannot disagree.
- **Invariants.** (1) A unit's box depends only on its own names and the rules: no
  collection statistic participates in replay, so a file added beside it cannot move it.
  (2) The walk emits rules, never assignments. (3) A grouper sees candidates, never units,
  so a wrong grouping can only merge boxes, never move a file. (4) The graph never places
  a file; it draws the arrows and audits the boxes. (5) Never refuse: a scope with one box
  falls through the ladder; a scope nothing splits is an honest leaf that says why.

## 3. The parts (`static_analyzer/clustering/names/`)

### 3.1 Units and the trie (`inventory.py`)

A **unit** is a file: the set of qualified names declared in it. Its **position** is the
longest prefix its names share, less a trailing symbol (a file declaring one class shares
that class across every member). The engine emits no module node, so this is how the
module of a Python file, the package of a Java type and the directory of a C# type are read
off the names alone. The **trie** is the prefix tree of positions; a dotted directory name
(`Ordering.API`) nests as two segments, which is what lets `Ordering.API`, `Ordering.Domain`
and `Ordering.Infrastructure` sit under one `Ordering` node that is never split along its
role-named children.

### 3.2 The frontier walk (`frontier.py`)

Per node, in order:

| node | rule |
|---|---|
| one child, no units | pass through |
| child is a layout word (`src`, `packages`, `pkg`, …) or holds ≥ 80% of the parent | step through, whatever it is called; this is decided before the layered test below |
| children mostly (≥ 60%) role-named, ≥ 3 of them | **layered**: if a feature name recurs under ≥ 2 distinct layer children, **transpose** onto those features; else one box |
| child is role-named | a box, never a way in |
| feature-named child holding ≥ 25% of the scope, with mostly feature-named children | open it |
| feature-named child holding ≥ 25% of the scope, with ≥ 3 mostly role-named children | layered, as above |
| anything else | a box; a one-unit child is a loose unit of its parent |
| a node the walk entered that has only units and one-unit children | one box (a flat scope) |

Role words are a fixed closed class (`ROLE_WORDS`, ~95 stems: Api, Domain, Models,
Services, Handlers, …) plus a per-repo tail the planner may add; measured, this list cannot
be learned from identifier frequencies. Ubiquity (a product namesake carried by most
siblings) is by frequency over ≥ 2 siblings, never by set intersection.

The walk emits **candidates**, each a bundle of rules: a *box* owns its trie path; a
*feature* of a transposed node owns the shallowest feature-named directory under each
layer plus its word; a *residual* owns a layer directory as a fallback; *loose* units of a
node own the node's path as a fallback. Loose units therefore vote with their words before
falling back to their directory: the per-unit fall-through that every replacement rule in
the edge-case study converged on. A fallback-only rule is the scope's last resort: it is
never absorbed into a neighbour and never counted by the guard, so it stays its own small
box, and a unit it catches is still reported as a new-scope candidate (§4).

### 3.3 The grouper (`draft.py`, `Grouper`)

The one judgement in the tree: turn the candidates of a rung into named components. Two
implementations behind one protocol, switchable by configuration and kept side by side:

- `KinshipGrouper` (deterministic, this PR): merge candidates sharing their distinctive
  word (`Ordering` + `OrderProcessor`, `Webhooks` + `WebhookClient`, `EventBus` +
  `EventBusRabbitMQ`). Recovers eShop's depth-1 drawing at 1.000 with no model.
- The planner (LLM, next PR): reads the kinship-merged candidates (names, unit counts, a
  sample of identifiers) and returns groups toward a 5–9 preference, names, and machinery
  words. It may merge across words (`apiserver` + `apimachinery`; django's docs themes),
  which no formula over names can. Its answer is validated (every candidate in exactly one
  group) and replayed verbatim. No silent fallback: a failed call fails the run.

### 3.4 The specification (`spec.py`)

`TreeSpec` = `{scopes: {scope_id: ScopeSpec}, machinery, grouper, version}`. A
`ScopeSpec` present with no rules is a leaf by decision; a scope absent from the spec was
never drafted (below the depth cap). Rule ids are the component ids (`1`, `1.2`), allocated
largest-first at draft time and stable thereafter; a new id is always above every id the
scope has used, never a refilled gap. Serialisation stores prefixes as segment lists, so a
segment may contain a dot or a generic argument list. Replay also depends on the tokenizer,
the stemmer and the fixed role words; a change to any of them bumps `version`, and a spec
of an older version is redrafted by the next full run.

### 3.5 The ladder (`draft.py`, `draft_scope`)

The first rung that yields two children wins; a rung yields children only if at least two
rules with a prefix or a word each hold `max(2, int(5% of the parent))` units, and a smaller
one is absorbed by the largest (the measured guard; fallback-only rules are exempt). The
root takes no guard on its frontier: a box the names draw is a box, and small ones are the
grouper's to merge. Ties in the word vote go to the rule that comes first in the scope,
never to an id's spelling, so ordering and numbering cannot move a unit.

| scope | rungs, in order |
|---|---|
| root | frontier of the trie → words of the units (only if the frontier gave one box) |
| component ≤ 135 units | un-merge: the candidates the grouper merged into it → leaf ("cohesive") |
| component > 135 units | un-merge → frontier of its own sub-trie → words of its units → leaf ("exhausted") |

Why the cap: on the one maintainer-drawn depth-2 ruler, un-merge alone scores 0.999 and
over-splits 1 of 7 leaf parents; the next-segment and vocabulary rungs score 0.50 and 0.55
and over-split all 7. 135 units is the largest box any maintainer has left unsplit
(eShop's client app). Above it a box is too large to read, so every name coordinate is
tried before it becomes a leaf. There is no graph tier: Leiden is retired at every depth.

## 4. Full, incremental, partial: one function, three entry points

**Full run.** Units from every language's graph → `draft_tree(units, grouper, depth_cap)`
→ `TreeSpec` persisted in `analysis.json` metadata → per scope, `replay` gives the
`ClusterGroup`s the agents already consume (one component per group, file leaves as the
cluster ids, connections from the graph's edges). The abstraction and details agents keep
naming and describing components; nothing in the agent contract changes.

**Incremental run.** No anchoring, no seeded Leiden, no drift budget. The frozen spec is
replayed over the live graph's names (the incremental engine carries the same keys for
changed and unchanged files). Per scope, walking the tree top-down:

1. `replay(units, scope)`. A component whose rule still claims units keeps its id and
   membership; `previous_component_id` is the rule id, so the existing scope plan emits
   UPDATE only where members or bodies changed, and nothing else moves. Measured: 0.00%
   of unchanged files move across 27 repositories and four edit types.
2. **New components.** Every unit that no prefix and no word claims, whether a fallback
   rule caught it or nothing did, is reported in `Partition.new_scopes` under the prefix at
   which its position first leaves every prefix a rule owns: a new top-level directory
   diverges at that directory; a new file deep inside a known one is claimed by its
   ancestor's rule and never gets here. A group of ≥ 2 units whose members are all new to
   the analysis (absent from the baseline membership) becomes a new rule appended to the
   scope, prefix only and without words so no existing unit can be re-voted, with a fresh id
   above every id the scope has ever used; the scope plan then emits CREATE and the
   incremental agent names it. A group that also holds units the baseline knew is a rule's
   residue and stays where it is. A lone new unit stays where its fallback put it, or in the
   unplaced bucket, which is created on demand. This is the same at every depth: a new
   sub-directory inside component `3` surfaces in scope `3` on the next run, as a new `3.k`;
   today it needed ≥ 16 files to isolate two Leiden clusters before it could appear at all.
3. **Retired components.** A rule that claims nothing is deleted by the existing scope
   plan (DELETE), and its child scope with it.
4. A component whose child scope was never drafted (the depth cap was raised) is drafted
   now with the configured grouper; its membership at its own level is unchanged.

A baseline without a `tree_spec` cannot support an incremental run and raises
`IncrementalCacheMissingError` (run a full analysis first), as today for a missing cluster
baseline. The cluster lineage in the pickle is no longer read.

**Partial run (expand one component).** Replay the stored spec for that scope; if the
scope is absent, draft it from the component's units. A leaf by decision stays a leaf: the
API reports it as not expandable with its reason.

**Depth.** `--depth-level` is the cap on how many scopes are drafted up front, never a
target. `expandable` on a group is "its scope has rules" (drafted and split) or "its scope
is absent and it exceeds the leaf cap or has parts to un-merge" (would split if drafted).
The modularity expansion gate and the size-based `scope_load` are retired.

## 5. What the graph does

Arrows: `GroupConnection`s from call and reference edges between groups, exactly as today,
at every depth. Audit (follow-up, not in this stack): per component, leakage, islands,
name-versus-call disagreements and residuals, persisted for the health layer and the
extension. The conjunction-name metric is not shipped (signal-to-noise ≈ 1).

## 6. What goes

| piece | verdict | why |
|---|---|---|
| `clustering/engine.py` (Leiden level-up search) | delete | leaves are files; no community is ever computed |
| `grouping.py`: modularity peak, seeds, absorption, `_anchored_group`, drift budget | delete | grouping is names; incremental is replay |
| `delta.py`, `snapshot.py` (seeded Leiden, cluster snapshots) | delete | no partition is diffed; the spec is replayed |
| `ClusterCache` lineage in the pickle, `_record_scopes`, `LanguageResults.clusters` | delete | the partition is a pure function of names and spec; nothing to persist. Removes the "no cluster baseline" failure mode, the top user-facing error |
| `expansion.py` modularity gate, `ClusteringConfig.EXPAND_MODULARITY_THRESHOLD`, `MIN_METHODS_TO_EXPAND`, `MAX_LEAF_*`, `GroupingConfig` | delete | expansion is a ladder decision recorded in the spec |
| `leiden_utils.py`, `igraph`, `leidenalg` | delete | no caller left |
| `ClusterScopeResult.modularity`, `unanchored_*`, `regrouped`, `AnchoredGrouping` | delete | nothing reads them once the scope plan stops reporting drift |
| `reindex_*`, `combine_cluster_results`, `group_symbols`, `ClusterResult`, `ClusterGroup`, `ClusterScopeResult`, `GroupConnection`, `scope_plan`, `tree_shape` | keep | the agent and incremental contracts are unchanged: file leaves are cluster ids |
| #539's `naming.py` | replaced | `tokenize`, `stem`, head-noun voting and the distinctive-word merge survive in `tokens.py` and `replay.py`; `scope_of`, `BUILD_ROOTS`, the repo-wide gate and the `Infrastructure` sink do not |

## 7. Measured

The implementation in this PR, fed the eight rulers with units synthesised the way each
adapter names things (no LLM, no LSP; harness in the research scratchpad), against the
zero-LLM probe the design was drawn from. Pair-F1 at file granularity / boxes.

| ruler | probe frontier | this frontier | kinship | probe kinship | depth 2 |
|---|---|---|---|---|---|
| beacon (layered C#) | 0.889 / 13 | 0.889 / 13 | 0.889 / 13 | 0.889 | leaves |
| eShop depth 1 | 0.979 / 12 | 0.979 / 12 | **1.000 / 9** | 1.000 | |
| eShop depth 2 | 0.999 / 12 | 0.999 / 12 | 0.980 / 9 | 0.980 | **0.999 / 12** |
| modulify | 1.000 / 4 | 1.000 / 4 | 1.000 / 4 | 1.000 | |
| django (Python) | 0.714 / 28 | 0.714 / 26 | 0.714 / 26 | 0.714 | |
| kubernetes (Go) | 0.240 / 74 | 0.240 / 75 | 0.252 / 64 | 0.273 | |
| mermaid (TypeScript) | 0.670 / 33 | 0.670 / 33 | 0.624 / 30 | 0.661 | |
| spring-framework (Java) | 1.000 / 22 | 1.000 / 23 | 0.974 / 21 | 0.974 | |

Shipped pipeline on the same rulers: eShop 0.667, django 0.235, mermaid 0.083 (both below
the all-in-one floor), Beacon 0.351. Phase-1 exit criteria from the artifact (django ≥ 0.70,
mermaid ≥ 0.65, spring 1.00, Beacon ≥ 0.85, eShop and modulify unchanged, eShop depth 2
≥ 0.99) are all met by the deterministic path alone; kubernetes (ceiling 0.88 after a
perfect grouping) is the planner's to reach.

## 8. Decisions taken

- **No graph tier.** An exhausted box is an honest leaf with a reason; Leiden and its
  dependencies go. A call-derived "implementation view" outside the tree is a possible
  follow-up.
- **Full qualified names vote.** Every segment of every name a unit declares (module,
  class, method) contributes to the word vote, head noun heaviest.
- **Two groupers, one interface**, selected by configuration; both stay until one is
  chosen on evidence. The deterministic one is the draft the planner edits.
- **Only static-analysis names are partitioned.** No support-scope carve-out.
- **Never refuse.** One box at the root falls through to the words; nothing splits it,
  one box is drawn and the structure diagnostics say so.
- **Aggressive rungs only above the leaf cap** (§3.5), because both over-split every
  maintainer-drawn leaf on the only depth-2 ruler.

## 9. The stack

1. **`feat/name-tree-core` (this PR).** This document; the `names` package (tokens,
   inventory, frontier, spec, draft, replay); unit tests over ruler-shaped layouts. No
   pipeline change; nothing is wired.
2. **`feat/name-tree-pipeline`.** `ClusteringService` on the partition for full, partial
   and incremental runs (file leaves, spec drafting, replay, new-scope detection, expansion
   from the ladder); the planner agent as the LLM `Grouper` with #539's prompt and
   evidence sampling; the `grouper` configuration switch; `tree_spec` in `analysis.json`
   with the read-back rules of #539/#542 (a run building on a baseline reuses the stored
   spec, never re-reads). Companion `CodeBoarding-tests` branch of the same name.
3. **`feat/name-tree-cleanup`.** Everything in §6 marked delete, the pickle tag bump, the
   dependency removal, and the tests that only exercised the deleted code.

## 10. Open items

- Kubernetes-scale repos over-produce root boxes (64–75 against 16 sigs); grouping them
  is the planner's job and its variance must be measured across draws before it ships.
- The vocabulary rung without a model over-splits; a planner-owned vocabulary (as in the
  measurements) would make it usable below the leaf cap. Not attempted here.
- The stemmer is crude (`spring` → `spr`); it is consistent with the measurements and
  ubiquity absorbs most of the damage, but it should be replaced when a ruler shows it
  costing something.
- Declared namespaces as merge hints (C#) once an adapter carries them as a side table.
- The weakness surface (§5 audit) and its rendering.
- The zero-LLM ruler harness and the four held-out truths (django, kubernetes, mermaid,
  spring) live in a research scratchpad; they belong in `CodeBoarding-evals` beside the
  eShop reference so the numbers in §7 can be re-run in CI.
