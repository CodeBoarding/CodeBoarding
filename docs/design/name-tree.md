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
- **The one function.** `replay(units, scope, role_words) -> Partition` (the role words
  being `role_words_for(spec.machinery)`) assigns every unit of a scope to exactly one rule by: the longest matching prefix, then a head-noun-weighted vote
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
| children mostly (≥ 60%) role-named, ≥ 3 of them | **layered**: if a feature name recurs under ≥ 2 distinct layer children, **transpose** onto those features (at the root and above the leaf cap; below it the node is one box, because a transposition leaves a residual per layer); else the layers are the boxes, the only structure there is |
| child is role-named | a box, never a way in |
| feature-named child holding ≥ 50% of the scope, with mostly feature-named children | open it: half the scope is the scope's structure, less is one of its parts |
| feature-named child holding ≥ 50% of the scope, with ≥ 3 mostly role-named children | layered, as above |
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

The one judgement in the tree: turn the candidates of a rung into named components. Three
implementations behind one protocol, switchable by configuration (`CODEBOARDING_GROUPER`,
`[clustering] grouper`):

- `KinshipGrouper`: merge candidates sharing their distinctive word (`Ordering` +
  `OrderProcessor`, `Webhooks` + `WebhookClient`, `EventBus` + `EventBusRabbitMQ`).
  Recovers eShop's depth-1 drawing at 1.000 with no model.
- `AffinityGrouper` (default): kinship, then fold the rung along the call graph. The
  context hands every grouper, per candidate, its size and the graph links (call edges plus
  `INHERITS`/`TYPEREF` reference edges, cross-file only) it exchanges with each sibling;
  the service computes them once per run and they never reach `replay`. A candidate below
  the scope's floor, and then the smallest candidate while the scope holds more than nine,
  joins the sibling with the highest *observed over expected* link count
  (`links × total / (degree × degree)`, at least two links, never past 60% of the scope).
  Why that ratio and not the raw count: a hub (`utils`, `core`) talks to everyone, so raw
  counts fold every small box into it; against its degree it is nobody's closest sibling.
  Measured on django it recovers what the LLM planner folded (`http` → `views`,
  `templatetags` → `template`, `conf`/`apps` → `core`, `urls`/`middleware` together) and
  agrees with the planner's root grouping at 0.96 pair-F1, with no model and byte-identical
  across runs. A candidate with no affine sibling stays its own box; the fold only ever
  merges. The persisted rules are still prefixes and words: a fold is one rule with the
  members' prefixes and the union of their words, so replay stays graph-free.
- `TreePlannerAgent` (LLM): runs kinship first and, only when a scope is left with more
  than nine groups, shows the model those groups with their sizes and a few identifiers and
  lets it fold them into components toward the budget. It may merge across words
  (`apiserver` + `apimachinery`; django's docs themes), which no formula over names can. Its
  answer is folded deterministically (every label lands in exactly one component, the first
  to name it; a forgotten label keeps its own group) and replayed verbatim. No silent
  fallback: asking for the planner without an LLM is an error. Measured against the
  affinity fold it is no better on the rulers and moves between draws (grouper study): the
  model is not deterministic at temperature 0 (its hidden reasoning is sampled; 0.5–0.7 pair
  agreement between two draws of one prompt on markitdown and serilog). So the planner asks
  in one JSON call with no tools, lists the groups largest first with the scope's floor, draws
  three answers concurrently and folds the medoid (the draw the other two agree with most), and
  keeps an `owns` word only when it is a lowercase stem found in the component's own identifiers
  and nobody else's, because replay votes on owned words at full weight and a package-wide word
  had pulled 35 of markitdown's 40 files into one box. Measured: markitdown draws agree at
  0.62 instead of 0.27–0.66 with 6–7 boxes instead of 2–7, serilog 0.69–0.79; three times the
  planner's tokens, the wall clock of one call.

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
rules with a prefix or a word each hold `max(2, int(5% of the parent))` units. A smaller
rule the grouper found no sibling for stays its own small box: folding it into the largest
rule was measured to turn django's `contrib` into a `gis` grab bag of twelve packages, and
the graph fold already sends everything that has a neighbour to that neighbour. The root
takes no guard on its frontier: a box the names draw is a box, and small ones are the
grouper's to fold. Fallback-only rules are exempt everywhere. Ties in the word vote go to
the rule that comes first in the scope, never to an id's spelling, so ordering and
numbering cannot move a unit.

| scope | rungs, in order |
|---|---|
| root | frontier of the trie (layers transposed or drawn) → words of the units (only if the frontier gave one box) |
| component < 40 units | un-merge: the candidates the grouper folded into it → leaf ("cohesive") |
| component 40–135 units | un-merge → frontier of its own sub-trie, layered nodes with a grid kept whole → leaf ("cohesive") |
| component > 135 units | un-merge → frontier of its sub-trie, transposed where a grid recurs → words of its units → leaf ("exhausted") |

Why two thresholds: the floor (40) is the size below which a box reads whole in a listing,
and above which a reader wants to click; the cap (135, the largest box any maintainer has
left unsplit, eShop's client app) is where the aggressive coordinates start, because on the
one maintainer-drawn depth-2 ruler a transposition below it over-splits every leaf parent
(eShop depth 2: 0.974 whole, 0.59 transposed at 40 units, 0.67 at 70). The structural
rungs below the cap cost that ruler 0.974 → 0.934 (Identity.API opens into Quickstart,
Models, Services, Data) and buy django eight expandable components at depth 2 where there
was one. The un-merge rung gives nesting for free: a root box folded from several
candidates opens into them at depth 2, and each of those opens its own sub-trie at depth 3.
There is no graph tier: Leiden is retired at every depth.

## 4. Full, incremental, partial: one function, three entry points

**Full run.** Units from every language's graph → `draft_tree(units, grouper, depth_cap)`
→ `TreeSpec` persisted in `analysis.json` metadata → per scope, `replay` gives the
`ClusterGroup`s the agents already consume (one component per group, file leaves as the
cluster ids, connections from the graph's edges). The abstraction and details agents keep
naming and describing components; nothing in the agent contract changes.

**Incremental run.** No anchoring, no seeded Leiden, no drift budget. The frozen spec is
replayed over the live graph's names (the incremental engine carries the same keys for
changed and unchanged files); a scope the spec never reached is drafted with the kinship
grouper, because on an incremental run the agents, and any LLM, come up only after
clustering. Per scope, walking the tree top-down:

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
   now; its membership at its own level is unchanged.
5. Whether anything changed is read off the replayed tree against the persisted one: a
   component gained, lost or re-membered. Only then do the agents initialise.

A baseline without a `tree_spec` cannot support an incremental run and raises
`IncrementalCacheMissingError` (run a full analysis first), as today for a missing cluster
baseline. The cluster lineage in the pickle is no longer read.

**Partial run (expand one component).** Replay the stored spec for that scope; if the
scope is absent, draft it from the component's units. A leaf by decision stays a leaf: the
API reports it as not expandable with its reason.

**Depth.** `--depth-level` is the cap on how many scopes are materialised up front, never a
target. The spec is drafted one level deeper than the tree is materialised, so `expandable`
on a group is a recorded decision: its child scope has rules. The modularity expansion gate
and the size-based `scope_load` are retired.

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
| kubernetes (Go) | 0.240 / 74 | **0.432 / 53** | 0.441 / 44 | 0.273 | |
| mermaid (TypeScript) | 0.670 / 33 | 0.670 / 33 | 0.624 / 30 | 0.661 | |
| spring-framework (Java) | 1.000 / 22 | 1.000 / 23 | 0.974 / 21 | 0.974 | |

The fold and the nesting, on six real static analyses (pickles, zero LLM; kinship at depth 2
is what the grouper study shipped). Expandable is per level; pair-F1 against the eShop
README diagram and the django/mermaid docs areas, which are floors rather than truths at any
depth but 1 for eShop.

| repo (units) | root boxes | expandable L1 / L2 | largest root box | F1 depth 1 | F1 depth 2 | F1 depth 3 |
|---|---|---|---|---|---|---|
| eShop (475) | 14 → **9** | 2/0 → **7/1** | 28% → 28% | 0.986 → 0.985 | 0.974 → 0.934 | 0.974 → 0.861 |
| django (635) | 18 → **9** | 1/1 → **7/8** | 46% → 47% | 0.423 → 0.410 | 0.545 → **0.709** | 0.662 → 0.434 |
| mermaid (386) | 12 → **9** | 2/1 → **4/2** | 44% → 47% | 0.086 → 0.075 | 0.099 → 0.087 | 0.142 → **0.229** |
| markitdown (40) | 3 → 3 | 0/0 → 0/0 | **88% → 57%** | | | |
| serilog (109) | 7 → **9** | 1/0 → **3/1** | **69% → 43%** | | | |
| fzf (42) | 5 → 5 | 0/0 → 0/0 | 57% → 57% | | | |

eShop's nine root boxes are the maintainers' nine at 0.985: the fold merges what the ruler
does not draw (WebAppComponents and HybridApp into WebApp, IntegrationEventLogEF into
Catalog) and one thing it does (PaymentProcessor and the AppHost into Basket, the only
cost). The maintainers' depth-1 boxes are recovered from the union of depth-2 boxes at 0.915.
markitdown and serilog no longer reach the vocabulary rung: their root is layered without a
grid, and the layers (`converters`; `Core`, `Events`, `Configuration`, `Parsing`, …) are
what the planner had folded the words into. Agreement with the planner's root grouping:
django 0.96, eShop 0.85, mermaid 0.83, unchanged from kinship. Every spec is byte-identical
across two runs and across unit order.

The probe opened a feature-named child at a quarter of the scope; this implementation opens
it at half, which the probe's own sweep showed identical on seven rulers and better on
kubernetes, and which keeps a repository with two large packages (CodeBoarding's
`static_analyzer` and `agents`) at its 13 top-level packages instead of scattering their
sub-packages across the root. Shipped pipeline on the same rulers: eShop 0.667, django 0.235,
mermaid 0.083 (both below the all-in-one floor), Beacon 0.351. Phase-1 exit criteria from the artifact (django ≥ 0.70,
mermaid ≥ 0.65, spring 1.00, Beacon ≥ 0.85, eShop and modulify unchanged, eShop depth 2
≥ 0.99) are all met by the deterministic path alone; kubernetes (ceiling 0.88 after a
perfect grouping) is the planner's to reach.

## 8. Decisions taken

- **No graph tier.** An exhausted box is an honest leaf with a reason; Leiden and its
  dependencies go. A call-derived "implementation view" outside the tree is a possible
  follow-up.
- **Full qualified names vote.** Every segment of every name a unit declares (module,
  class, method) contributes to the word vote, head noun heaviest.
- **Three groupers, one interface**, selected by configuration. The affinity fold is the
  default: it reaches the planner's root grouping without a model and without variance; the
  planner stays as the escape hatch for what no formula over names and links can group.
- **Only static-analysis names are partitioned.** No support-scope carve-out.
- **Never refuse.** One box at the root falls through to the words; nothing splits it,
  one box is drawn and the structure diagnostics say so.
- **Structure from 40 units, aggressive rungs only above the leaf cap** (§3.5): the
  sub-trie of a component is drawn from 40 units, transposition and words only above 135,
  because a transposition below the cap over-splits every maintainer-drawn leaf on the only
  depth-2 ruler while a directory split costs it 0.04.
- **The guard never folds into the largest rule.** A weak rule with no neighbour in the
  graph stands; the fold, not the guard, decides where a small box goes.
- **The graph folds, never places.** Links between candidates reach the grouper as a
  context; the persisted rules stay prefixes and words, and replay never sees an edge.

## 9. The stack

1. **`feat/name-tree-core` (this PR).** This document; the `names` package (tokens,
   inventory, frontier, spec, draft, replay); unit tests over ruler-shaped layouts. No
   pipeline change; nothing is wired.
2. **`feat/name-tree-pipeline`.** `ClusteringService` on the partition for full, partial
   and incremental runs (file leaves, spec drafting, replay, new-scope detection, expansion
   from the ladder); the planner agent as the LLM `Grouper`; the `grouper` configuration
   switch; `tree_spec` in `analysis.json` with the read-back rules of #539/#542 (a run
   building on a baseline reuses the stored spec, never re-reads); a created component keeps
   the id the spec allocated. The service tests of the old internals go with the internals;
   the modules they exercised stay until the next PR. Companion `CodeBoarding-tests` branch of
   the same name.
3. **`feat/name-tree-cleanup`.** Everything in §6 marked delete, the pickle tag bump to v7
   (the pickle no longer carries cluster lineage), the removal of `igraph` and `leidenalg`,
   and the tests that only exercised the deleted code. `ClusteringService` no longer takes or
   records cluster caches; `absorb_single_child_components` no longer reroots them.

## 10. Open items

- Kubernetes-scale repos over-produce root boxes (44–53 against 16 sigs); grouping them
  is the planner's job and its variance must be measured across draws before it ships.
- The vocabulary rung without a model collapses onto the commonest word (markitdown 88%,
  serilog 69% in the grouper study). After the layered root draws its layers no repository
  in the set reaches the rung, so the co-occurrence grouping of its words the study proposed
  is unmeasured and not attempted; the affinity fold applies to word candidates as to any
  other, which is all the rung has today.
- The fold is measured on repositories of at most 635 units; kubernetes-scale roots (44–53
  candidates) need links the synthesised ruler harness does not carry.
- A unit the parent placed by a word has no home among the un-merged parts and lands in
  the unplaced bucket (eShop's Basket, serilog's Core: two and three files).
- The stemmer is crude (`spring` → `spr`); it is consistent with the measurements and
  ubiquity absorbs most of the damage, but it should be replaced when a ruler shows it
  costing something.
- Declared namespaces as merge hints (C#) once an adapter carries them as a side table.
- The weakness surface (§5 audit) and its rendering.
- The zero-LLM ruler harness and the four held-out truths (django, kubernetes, mermaid,
  spring) live in a research scratchpad; they belong in `CodeBoarding-evals` beside the
  eShop reference so the numbers in §7 can be re-run in CI.
