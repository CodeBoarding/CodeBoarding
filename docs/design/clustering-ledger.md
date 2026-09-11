# Clustering: what has been tried

A ledger of every approach measured or reasoned out for the component partition: what was
tried, what came out, and why it was kept or discarded. Add a row whenever something is
measured; never delete a row; when new evidence changes a decision, change the decision column
and say what the evidence was. Companion to `name-tree.md` (the design as it stands).

Decisions read **kept**, **discarded**, **kept with a limit** (works in some cases, bounded
where it costs) or **not run** (discarded on an argument stated in the row; measure it before
disagreeing).

Sources: `[design]` = `name-tree.md`; `[handoff]` = `CLUSTERING-HANDOFF.md` in the workspace;
`[eval]` = `PR586-analysis.md` (five unseen repositories); `[fix]` = `PR586-limit-fix.md`;
`[study X]` = the named study in CodeBoarding-tests or CodeBoarding-evals; `[notes]` = session
notes, with dates.

## 1. What is partitioned, and where structure comes from

| what we tried | what came out | decision, and why | source |
|---|---|---|---|
| Leiden communities on the call graph as leaves, a modularity peak to group them, seeded Leiden on incremental runs | eShop's graph has 204 connected components against 10 published boxes and no resolution keeps one box whole and separated; 84–93% of leaf clusters had no inter-cluster edge, so 28–40% of methods were placed by a tie-break; the shipped pipeline scored below the all-in-one floor on two of four rulers | **discarded.** The call graph can validate a partition but cannot generate one; the boxes come from what things are called | [design §1], [notes clustering ceiling 24 Aug], [notes partitioner rebuild 15 Aug] |
| Leaves are files, positioned by their directory under the repository root | Beacon 0.34 → 0.94, eShop 0.67 → 1.00 against the rulers | **kept.** A file pools its identifiers and crosses no boundary in any ruler; leaf clusters crossed the boundaries names want to keep | [design §2], [notes qualified-name clustering 31 Aug] |
| Declared C# namespaces as the primary key | on eShop 125 of 213 namespaces differ from the path only by a product root, and where they differ the namespace is coarser (all `Platforms/*` collapse); maintainers put vendored IdentityServer files in the project box | **discarded as primary, allowed as a merge hint.** When namespace and project disagree, the project wins | [notes qname defect 2 Sep], [notes namespace grouping 25 Aug] |
| Qualified names parsed for structure (adapter spellings, the engine root of a nested solution) | abp's `framework/` invisible, btcpayserver transposed into 75 word boxes, eShop's two JS files a box of their own | **discarded.** Positions come from paths; every adapter now spells names from the repository root, so name and position agree | [design §3.1], [handoff §3.1, §3.5] |
| Frontier opens a child holding ≥ 25% or ≥ 50% of its scope | Polly 21 root boxes, AutoMapper 12, LMCache 26, btcpayserver 27 | **discarded; only stepping through a child at ≥ 80% survives.** Opening scattered a directory's contents across its parent. Measured before path positions, when Polly's `Polly` namespace held over half; under path positions Polly's largest child is 37%, so the Polly part of the evidence is stale | [notes budget study 10 Sep], [design §3.2] |
| Layout words (`src`, `lib`, …) stepped through by name | replaced by the 80% rule with no loss on the rulers | **discarded.** A list of names cannot be complete; the share rule needs none | [notes name-tree stack 2 Sep] |
| Transposing a layered node onto recurring feature names | Beacon 0.70 → 0.889 once features must recur under two layers; below 135 units it over-splits every maintainer-drawn leaf on eShop depth 2 (0.974 whole, 0.59 transposed at 40 units, 0.67 at 70) | **kept above the leaf cap only.** Below it a grid is drawn layer by layer | [design §3.5, §8] |
| Folding one-unit subtrees into the trie | erased the one-file feature directories the transposition needs; Beacon 0.70 → 0.889 once un-folded | **discarded.** A one-unit subtree is a loose unit of its parent at walk time, kept in the tree | [notes name-tree stack] |
| Role vocabulary learned from identifier frequencies | `Incident` outranks `Handler` on Beacon; learned-only Beacon 0.24, eShop 0.61 against a fixed list's 0.89, 0.98 | **discarded.** Closed-class layer names never appear as identifier heads; the list is fixed, the planner may add a per-repo tail | [notes qualified-name clustering], [design §3.2] |
| Ubiquity (a product namesake) by set intersection over siblings | one odd sibling keeps the product name alive as everyone's distinctive word | **discarded.** Frequency over at least three siblings and half of them | [design §3.2] |
| Dotted C# directory names nested as trie segments (`Ordering.API` → `Ordering/API`) | makes `Ordering` one node with role children, un-merged into Ordering + OrderProcessor at depth 2 | **kept.** Merging dotted directories into one segment was only ever a fix for the old name-derived trie | [notes name-tree stack], [notes budget study] |
| Module-first from build manifests, Leiden per module | eShop 0.235 → 0.667 and 1/12 → 7/12 boxes, but abp's largest component 22% → 58% and the action repository collapsed to one box | **superseded.** The right unit (files) plus names does what modules did without the collapse | [notes partitioner rebuild] |
| Tests, docs and samples in a separate scope | never measured as a partition rule | **discarded by product call.** Only static-analysis names are partitioned; consumers are ordinary candidates that neither take nor fold | Svilen, 2 Sep [notes name-tree stack] |

## 2. Grouping the frontier's candidates (the fold)

| what we tried | what came out | decision, and why | source |
|---|---|---|---|
| Kinship: merge candidates sharing their distinctive word | eShop depth 1 0.979 → 1.000 (9 boxes), deterministic; letting a component's owned words merge instead pulled PaymentProcessor into Ordering (0.84) | **kept, always first.** Namesake grouping is deterministic; cross-token grouping is not | [design §3.3], [notes qualified-name clustering] |
| An LLM planner grouping the candidates (three draws, medoid) | no better than kinship on the rulers (eShop 0.986 vs 0.88), 6/7/2 boxes across three draws on markitdown, and its names never ship because the abstraction agent renames every box | **kept as an escape hatch only.** Variance and cost without a measured gain | [study grouper, tests#32], [design §3.3] |
| Affinity by observed-over-expected links (degree-normalised) | sent WebAppComponents to HybridApp on 3 links over WebApp on 15; folded every service into the event bus once the recovered call sites made the bus everyone's busiest partner | **discarded.** The ratio rewards quiet partners; the hub exclusion does what it was meant to do, by name | [handoff §5], [notes hub-fold study 10 Sep] |
| Raw link counts, a hub (called by 40% of siblings, three at least) neither absorbs nor folds | eShop 0.600 → 1.000 on the recovered graph; abp root 9 → 50 and Polly 9 → 21 until a limit existed; in MassTransit's core 22 of 38 candidates are hubs, the sixteen non-hubs become the only homes and everything small drains into them (the funnel) | **kept for the budget fold, with the limit and the dominance rows below.** Right in sparse scopes; the dense case needs a dominance test, not a different hub | [notes hub-fold study], [eval], [fix §1] |
| A hub as a last resort past the budget | abp 62% grab bag | **discarded.** Infrastructure absorbing its users is the picture nobody wants | [handoff §5] |
| Exactly nine boxes everywhere | eShop 7/9 | **discarded.** Nine is a target along links, never a count to force | [handoff §5] |
| Modularity (CNM) over the candidates | eShop 4/11, abp 21 boxes | **discarded.** No gain over links with the hub exclusion | [notes budget study] |
| Capping hubs to the top few by fan-in, or a density-relative hub threshold | not run | **not run.** The all-hubs case is what makes a single-package library right (jackson-databind: 10 of 15 root candidates are hubs, the directories stand, and the eval called it the win); the funnel comes from folding on "most links" without a dominance test, and fewer hubs only widens the set of sinks | [fix §1], this ledger |
| Vocabulary merge over the limit (closest TF-IDF pair) | merged nothing on the wide scopes it was written for (abp `framework`: the budget fold alone takes 76 → 15); once hubs may join it, a merged vector is closer to everything and it snowballs into a 1,183-file "Configuration" (59% of MassTransit's core) | **discarded.** Identifier vocabulary inside one namespace is noise, and pair merging is a snowball | [fix §1] |
| Pool of the smallest past the limit, hubs spared | MassTransit's core: 23 children, a 344-file "Other files" made of every non-hub, a 7-file hub standing beside it | **discarded.** A limit that spares most candidates cannot act | [eval §4] |
| Pool of the smallest past the limit, roles ignored | 39 → 15 on MassTransit's core; jackson-databind unchanged; nothing else moved | **kept.** In a dense scope the hub label carries no information about size | [fix §2] |
| Dominance for a small candidate: its home must carry half of the links it could follow (links to siblings able to take it) | alone it passes every funnel fold in a dense scope, because the followable slice is a sliver of the candidate's links (Topology → MessageData: 33 of 54 followable, 33 of 1,141 in all) | **kept together with the next row.** Right denominator for a home above the floor (MessagePack joins Abstractions on 53% of what it could follow), wrong alone for a small home | [fix §1] |
| A small home must also carry a quarter of *all* the candidate's links | separates every funnel fold (at most 0.18 of the source's links) from every family kept (at least 0.33) on MassTransit and abp | **kept.** Two small candidates joined are a new box under one of their names, which needs a real share of the links; 0.25 sits in the measured gap, not on a principle | [fix §2] |
| Dominance everywhere: a quarter or a third of all links for every fold, one rule | roots under the limit inflate: small satellites whose main partner is the hub stand (MassTransit 12 root boxes, five under 1%; hugo 15; jackson 15); a third refuses RabbitMQ → EventBus at 0.328 | **discarded.** The evidence a detail needs depends on what it joins | this session, sweeps `xD25/xD33/xD50` |
| A standing box absorbs any small candidate on most links (no share test when the home is big) | the home becomes a sink once it crosses the floor: abp's `Ddd.Application` 135 → 1,622 files in 45 consecutive folds | **discarded.** A big box has many links to everyone; "most links" is not evidence | this session, variant `wPBW` |
| Peer-size test instead of the share test (source at most half the home) | not run | **not run.** A small home grows with each absorption, so a chain of small candidates passes the size test one at a time and builds the bag anyway; the share test is a property of the source | this session |
| Freezing the link matrix at the original candidates | not run | **not run.** Removes the snowball but not the funnel: MessageData is genuinely the best non-hub partner of the small directories | this session |
| "Shared by three" counting hub callers; only non-hubs may own a helper | without it, in a scope where most siblings are hubs, every shared candidate reads as the helper of the few that are not (MessageData, called by 8 siblings, became JobService's helper as "shared by two") | **kept.** What three siblings call is shared, whoever they are | [fix §2] |
| A candidate under three units follows its links wherever they lead | keeps eShop's two-file `Shared` inside Catalog instead of on the root diagram | **kept.** The leaf ladder already needs one-file candidates to fold on most links; two files are never a box | [fix §2] |
| One fold rule replacing the role placement (helper, application, shared, project) | same aggregate numbers with fewer rules, but eShop changes below the root (ClientApp's 5-file `Animations` layer folds into `Services`; `Shared` moves to Ordering) and two-child scopes rise 51 → 63 | **discarded.** The application rule (called by none: a service, not a helper) is direction, which one undirected rule loses; the change on eShop has no reason a ruler can show | this session, variants `one`…`four` |
| At-the-floor candidates fold within budget when a sibling holds half of all their links | a 20-file box with three links joins a 6-file box; dropping the rule entirely leaves twelve equal parts of an un-merge unfolded | **kept only past the budget**, as shipped. Count pressure is what makes the rule safe | this session |
| A project root is never a helper | added for Polly.Extensions-like packages; projects already fold past the budget, so the rule acts within budget only | **kept, weakly.** No measurement either way | [design §3.3], `test_a_project_root_is_never_a_helper` |
| Opening a dominant directory at the root (its directories as candidates), at 0.4–0.6 of the scope | LMCache 71% → 14 boxes with three under 1%; btcpayserver 66% → 14 boxes and a worse subtree; at 0.45 MassTransit's root grows a 503-file "Other files" | **discarded.** Twenty to fifty candidates dumped into a root that the fold then pools | [fix §4] |
| Promoting a dominant directory's own fold among its siblings, at 0.4–0.6 | draws main's root for MassTransit at 0.45 (14 boxes, none under 1%) and lifts abp's depth-1 V 0.28 → 0.50 at 0.5; but the promoted boxes are folded again under a hub threshold and a kinship ubiquity computed over the new count (satellite projects merge by a shared word, two "Other files" at one root), and the threshold sits one point above MassTransit's share | **not shipped.** Would need promoted boxes protected from the parent's fold, pools merged and ubiquity computed before promotion; Svilen does not require a bound on a directory's share | [fix §4], Svilen 11 Sep |
| Dependency-fingerprint proxy for wide flat scopes (packages that depend on the same hubs) | not run | **open.** Proposed for abp's `framework` (100 packages → families) | [handoff §6.5] |

## 3. Depth: the ladder below a component

| what we tried | what came out | decision, and why | source |
|---|---|---|---|
| Un-merge: a fold's parts offered to the grouper again inside their scope | eShop depth 2 0.999; abp's widest depth-2 scope 31 → 10 from this alone | **kept, always the first rung below the root.** A box folded from seventy candidates opens onto nine, not seventy | [design §3.5], [notes hub-fold study] |
| Next trie segment below the leaf cap | over-splits 7 of 7 leaf parents on eShop depth 2 (0.496) | **kept above 135 units only** | [notes name-tree stack] |
| Vocabulary rung (one candidate per word of the names) | 51- and 60-child scopes, nothing folds because word candidates share no links; markitdown 88% and serilog 69% in one box when the frontier yields one box | **discarded** | [design §3.5], [study grouper] |
| Files rung (each file a candidate, kinship, graph fold, sub-floor groups pooled into "Loose files"), then a roles rung | files in leaves of at most seven: junit4 100%, eShop 93%, Polly 91%; cross-child leakage 0.35 against 0.79 for a random partition; almost no one-file leaves once the loose bucket exists | **kept** | [study leaf ladder, tests#34], [design §3.5] |
| Declaration or method units below the file | equal the file in C#/Java, split 30–71% of Python/TS/Go files, group same-named functions, worse than random by calls on serilog, junit4 and Polly | **discarded** | [study leaf ladder] |
| Island rung (one family against "Other X" when no link crosses) | fires on exactly two leaves in nine repositories, both right; the share-only variant made 14 arbitrary hub cuts | **kept as the last rung, with the link test** | [study leaf island, #563] |

## 4. Counts and bounds

| what we tried | what came out | decision, and why | source |
|---|---|---|---|
| Budget 9 as a soft target reached along links | folds small candidates toward nine; forced, it damages small roots (serilog 13 → 9: agent exact 1/8 against 5/8) | **kept as a target, never a count to force** | [notes budget study], [design §3.3] |
| Limit 15 inside the affinity grouper's pool | no limit for the kinship or planner groupers; the fallback rule `_settle` appends can make 16; hubs spared made 23 | **discarded as the place for the limit** | [eval §5], [fix §5] |
| Limit 15 in the ladder (`_settle` pools the smallest rules, room left for the fallback) | 0 scopes over 15 on 22 graphs, for every grouper | **kept.** The one function every rung passes through is where a guarantee belongs | this session |
| Cap 60% on folds | a fold never makes a box that is most of its scope; a directory can still be that big (LMCache `v1` 71%, btcpayserver's app 66%) | **kept as a default for folds; not an invariant for directories.** Svilen: such a box may be right and split more evenly inside; depends on the case and the graph | Svilen, 11 Sep |
| Floor 5% (`max(2, 5%)`) as "small enough to be a detail" | a 5% directory can be a core piece, rarely, and then it shows in the graph (a hub under the floor already stands) | **kept as a default** | Svilen, 11 Sep |
| The 5% guard applied to the root frontier | kubernetes 74 boxes → 5 grab bags; identical on eShop and abp | **discarded.** The root takes no guard | [notes name-tree stack], [notes hub-fold study] |
| Widening the old Leiden bracket [5,8] | the modularity peak sits at 8 whatever the bracket; forcing N regresses on eShop | **discarded** | [notes namespace grouping], [notes eShop fixture] |
| Equal-sized children as a goal | across 22 graphs the median scope's largest child is 45–50% of it and 2.5–2.7× the median child, unchanged by every fold variant | **not a goal.** Sizes follow the directories | Svilen 11 Sep, `sizes.py` |
| Components growing past the limit on incremental runs (a rule appended per new directory, nothing moved) | by design today | **deferred.** Svilen: not indefinitely, later | Svilen, 11 Sep |

## 5. Rulers and harnesses

- eShop's published architecture (`CodeBoarding-evals/src/codeboarding_evals/reference/eshop-depth-{1,2}.mmd`): the only maintainer-drawn ruler; compare module sets, never names; the depth-2 pair-F1 is meaningless (one same-box pair), use boxes reproduced (10/11).
- abp: V-measure against `.csproj` projects at depths 1 and 2; depth 1 is 0.28 by construction (6 boxes over 200 projects).
- Beacon, modulify, django, kubernetes, mermaid and spring-framework rulers over synthesised units in the name-tree research harness.
- Hold-out protocol with five unseen graphs (`CodeBoarding-evals/runs/hub-aware-fold-holdout`): invariants gate, rulers tie-break, a repository that exposes a defect joins the tuning set.
- Zero-LLM sweep over 17 tuning and 5 hold-out graphs in about 30 s: `CodeBoarding-Workspace/tools/cluster-study/limit-fix/`.
- Validation order (Svilen, 11 Sep): eShop first at every depth, then one repository picked blind, each compared against the previous approach; continue if worse but sensible, restart if worse repeatedly. Priority: C#, Java, TypeScript, Python.

## 6. The limit problem: candidates assessed before running (11 Sep)

Discarded without a run, on the rows above: hub-count caps and density-relative hubs (§2), dominance everywhere (§2), the peer-size test (§2), freezing links (§2), the vocabulary merge in any form (§2), a limit inside one grouper (§4), opening a dominant directory (§2, and Svilen does not require it).

Left standing, run in the protocol order from base #586 (eShop first at every depth, then the
blind pick, then MassTransit, then all 22 graphs):

1. The limit moves into the ladder (§4). **Measured:** eShop and jackson-databind identical to
   base; MassTransit's core 23 → 15 children, but the fifteen include a 338-file box named
   "JobService" (the bag the eval described, under a worse name) beside a 172-file pool of the
   smallest hubs. Necessary, not sufficient.
2. On top: the vocabulary rung deleted, "shared by three" counting hub callers, and the budget
   fold's criterion replaced by dominance (half of the followable links; a quarter of all links
   for a small home; under three units follows its links). **Measured:** eShop identical at
   every depth (26 scopes); jackson-databind 12 → 14 root boxes, `annotation` (15 files) and
   `jsonFormatVisitors` (19) out of a 35-file loose bucket, one root-level file left as a
   one-file "Loose files"; MassTransit's core 15 = the fourteen largest directories plus a pool
   of the twenty-four smallest, JobService and MessageData standing; across the 22 graphs no
   scope over fifteen, 12 pooled scopes (base 9), roots identical on 20 of 22 (hugo 11 → 14
   with four boxes of 2–5 files that base folded on two-link slivers), two-child scopes 51 →
   55, abp depth-2 V 0.687 → 0.717. **Kept.** Write-up: `PR586-limit-fix.md` in the workspace.
3. Not on top: replacing the role placement with one rule (§2), for the reason in its row.
