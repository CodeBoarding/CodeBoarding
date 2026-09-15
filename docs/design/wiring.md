# The wiring layer

The contract for the pass that draws what the call graph cannot see: the arrows and the resource
nodes that connect services at runtime. Every PR in the stack (§11) implements a part of this page
and nothing outside it. Companions: `WIRING-EDGES-RESEARCH.md` in the workspace (the evidence and
the reasoning), and `docs/wiring/` in CodeBoarding-evals (the rulers, the scorer, the baseline).

## 1. What it is for

A diagram today draws only what a language server sees: calls inside one language's world.
Everything that connects services at runtime — a compose file, a Kubernetes manifest, an Aspire
AppHost, a connection string, a gateway route, a registry — is invisible, so on a multi-service
repository the boxes float. On the seven rulers eShop draws 9 of its 23 arrows, all of them calls
into the event-bus library; every other ruler draws none.

The wiring layer reads those files, joins what they declare to the code that uses it, and adds
the arrows and the resource nodes the maintainers' own pictures draw. Three rules hold throughout:

- **No model call anywhere.** A verb comes from an edge's kind, a node's name from what the
  repository declares.
- **Deterministic.** Two runs over one tree produce identical edge sets, on macOS and in the
  action's Linux container, with no toolchain installed.
- **Wiring edges never move a box.** They draw arrows and pick verbs. The partition is decided by
  the structure the clustering already reads. (Measured: with shared-contract references allowed
  to move files, eShop's Payment service folded into the bus.)

## 2. Vocabulary

| term | meaning |
|---|---|
| unit | a directory that builds or deploys as one thing: a .NET project, a Maven module, an npm workspace package, a Python package, a compose service, a Kubernetes workload, a skaffold artifact, an Aspire resource. A unit has one directory and the names it answers to (aliases) |
| anchor | a place in the repository where a wiring key is declared (`def`) or used (`use`): a file, a line, the key as written, the key normalised, the unit the file belongs to |
| join | matching a `use` anchor to a `def` anchor of the same family by normalised key, in dependency order (§6) |
| resource node | a thing the code talks to that holds no source here: a database, cache, store, broker, gateway, third-party API, or actor |
| family | which kind of file a key lives in: A1 build graph, A2 deployment topology, A3 interface contracts, C1 environment and configuration keys, C2 service names, base URLs and gateway routes, C3 HTTP routes in code, C4 string-keyed messaging, D1 type-keyed messaging, E2 generated code, F3 database access |
| tier | T1 an exact key, T2 a template (a route with parameters collapsed), T3 a heuristic. Only T1 and T2 make edges |

## 3. Where it runs

A post-merge, language-agnostic pass at the end of `StaticAnalyzer.analyze`: after the per-engine
results are merged (full or warm-started) and before `_validate_analysis_results`. It is
language-agnostic because its edges cross engines: a compose file wires a Python service to a Java
one, and the per-engine `LanguageAnalysisResult` is single-language by construction.

- **Flag:** `CODEBOARDING_WIRING=1`, off by default until the stack is complete. With the flag off
  the pass does not run and every document is byte-identical to today's.
- **Storage:** a sibling bucket `StaticAnalysisResults.wiring`, never a `Language` key (a
  synthetic language trips the `LANGUAGE_EXTENSIONS` assertion). It holds the unit table, the
  anchors, the edges, the resource nodes and the diagnostics. The pickle tag bumps once for the
  layout (v12).
- **Files it reads:** manifests (`*.csproj`, `*.sln`, `pom.xml`, `package.json`, `pyproject.toml`),
  deployment (`docker-compose*.y*ml`, `compose*.y*ml`, Kubernetes manifests, `skaffold*.yaml`,
  Helm charts, Dockerfiles, an Aspire AppHost), configuration (`.env*`, `appsettings*.json`,
  `application*.y*ml`, `bootstrap*.y*ml`), nginx configuration. It reads these even where
  `.codeboardingignore` ignores dotfiles and `*.config.*`, read-only, and never reads
  `node_modules`, `bin`, `obj`, `dist`, `.git` or a test directory.
- **Cost:** under a second on every ruler (the P0 probes take 0.1–8 s), against static phases of
  one to ten minutes. The budget is 5 % of the static phase.

## 4. Nodes

| node | key | where it sits |
|---|---|---|
| code symbol | as today, `file\|qualified name` | its component |
| artifact file | a node of type FILE whose qualified name is its repository-relative path — the compose file, the manifest, the configuration file an edge is anchored in | the component owning its directory; a per-service Dockerfile or manifest goes with the unit it builds (P3) |
| resource | `resource:<kind>:<name>`, and `resource:<kind>:<name>/<kind>:<child>` for a database on a server or a route on a gateway | a resource node is never a code component and never counts toward the component budget; where it is drawn is §7 |

A resource's `name` is what the repository declares for it — the compose service, the Kubernetes
workload, the Aspire resource — never the image. The image decides the kind.

## 5. Edge kinds

`EdgeKind` in `static_analyzer/cfg/edge.py` is the policy surface: one row per kind saying what a
relation drawn from it alone is called, whether it is drawn as a component relation, whether it may
move a file, and whether it is infrastructure. Every consumer reads that table and nothing else.

| kind | from → to | verb | drawn | affine | infrastructure | phase |
|---|---|---|---|---|---|---|
| `CALL` | symbol → symbol | calls | yes | — | no | today |
| `CONTAINS` | member → class | contains | no | no | no | today |
| `INHERITS` | class → base | inherits from | no | yes | no | today |
| `TYPEREF` | symbol → type | uses | no | yes | no | no producer |
| `IMPORT` | module → module | imports | no | no | no | no producer |
| `DEPENDS_ON` | unit → unit (project reference, workspace dependency) | depends on | yes | no, gated later | no | P1 |
| `CALLS_HTTP` | unit or symbol → unit or handler (service name, base URL, later a route) | calls over HTTP | yes | never | no | P1 config, P2 code |
| `ROUTES_TO` | gateway route → unit or handler | routes to | yes | never | no | P1 |
| `USES` | unit or symbol → resource | uses | yes | never | no | P1 |
| `REGISTERS_WITH` | unit → registry | registers with | yes | never | yes | P1 |
| `FETCHES_CONFIG` | unit → configuration server | fetches configuration from | yes | never | yes | P1 |
| `REPORTS_TO` | unit → telemetry collector | reports to | yes | never | yes | P1 |
| `CALLS_RPC`, `PUBLISHES`, `HANDLES`, `READS`, `WRITES`, `IMPLEMENTS_CONTRACT`, `GENERATED_FROM`, `LINKS_NATIVE` | | | | | | added with their producer, P2 and later |

Rules that follow from the table:

- **A relation's verb** is the verb of the kind that connects the pair when no call does. A call
  outranks everything; otherwise the kind with the most edges names the relation, ties broken by
  declaration order (`static_relation_label`).
- **A default label is remembered as such.** A relation whose label is the static verb carries
  `default_label`; a later run recomputes it rather than carrying it as if someone had written it.
  In `analysis.json` the flag is written only when the verb is not `calls`, so documents with only
  calls in them do not change.
- **Kind on the edge.** Every `key_edges`/`all_edges` entry carries `kind` when it is not a call;
  a call edge is written as today. The wrapper's edge identity excludes the kind, so an upgraded
  document does not show every edge as changed.
- **Sites.** A reference edge carries the sites that made it (file, line, column). Two edges are
  the same edge when they join the same names the same way; sites never decide identity.

## 6. Joins

Seven rules, each paid for by a measured failure in the research appendices.

1. **Never join on a bare name.** A key carries its namespace: verb plus full path, env name plus
   the unit that defines it, topic plus broker, service name plus the topology it was declared in.
2. **Unmatched anchors stay visible.** A `use` with no `def` becomes an `unresolved:<family>:<key>`
   diagnostic; a `def` nobody uses is reported. Nothing is dropped silently.
3. **Joins run in dependency order.** Units first; then env, connection and service names, which
   need the unit an anchor sits in; then routes and topics, whose host resolves through an env
   value.
4. **Only literal or constant-folded keys join.** A key assembled at runtime is T3 and never an
   edge.
5. **Record which families found each edge.** Two independent mechanisms per arrow is the
   confidence signal, and attribution is what the scorer reports.
6. **Environment variants are unioned, not chosen.** Edges from `appsettings.Production.json`,
   `.env.production`, a Kustomize overlay or a compose override carry the environments they hold
   in. Compose files are merged by service name the way compose itself merges them, so a service
   is one unit whether its image and its build context come from one file or two.
7. **Ambiguity produces no edge.** One key with several providers emits nothing and reports both
   candidates.

Two more, from the P0 ceilings:

8. **Paths are canonical.** A unit directory is spelled from the repository root however the
   manifest reached it (`context: ../../../` plus `project: src/ledger/x` is `src/ledger/x`).
9. **A host resolves to a unit only when it is a name the topology declares.** A public FQDN in a
   documentation URL never falls back to its first DNS label.

A compose file describes no system when no service in it is built from the repository: per-test
infrastructure, dev containers, CI build images and templates produce no units and no resources.
Test directories and files named as tests (`*.test.*`, `*.spec.*`, `*_test.*`, `*Tests/`) anchor
nothing.

## 7. Resources

- **Kind** comes from a catalogue over image names, well-known env keys and configuration keys:
  `postgres`, `mysql`, `mssql/server`, `mongo` → db; `redis`, `memcached` → cache; `minio`, S3 → store;
  `rabbitmq`, `kafka`, `nats` → broker; `nginx`, `envoy`, YARP → gateway; a third-party endpoint →
  api; a browser or a customer's application → actor. The same catalogue gives a display name
  (`mssql/server` → SQL Server); the model may describe, never rename.
- **Children.** Connection strings name the databases on a server, route tables the routes on a
  gateway. A server with several databases is one node with children; the scorer accepts either
  rendering, one node with an arrow per user or each child inside its owner.
- **Home** — the box a resource belongs to: the owner of its content-defining declaration (the
  migrations, the exchange setup, the route table), else its sole user, else the lowest common
  ancestor of its users. A manifest that only runs a resource does not decide its home.
- **Level.** A shared resource is a peer where its users meet. A level draws at most 15 nodes,
  resources counted, actors counted. Over the cap, fold in this order: private resources into their
  owner (a badge, shown when the owner expands); registry, configuration and telemetry resources
  into one Infrastructure node that expands; remaining third parties into one External services
  node. On PetClinic this renders 13 nodes with three badges where the picture draws 16 peers; the
  scorer accepts both.
- **Infrastructure** flows (`REGISTERS_WITH`, `FETCHES_CONFIG`, `REPORTS_TO`) are drawn
  de-emphasised and collapsible: a star of such arrows says the same thing about every service.

## 8. Output contract

`analysis.json` gains, additively:

- `kind` on relation edges when it is not `call` (§5).
- `default_label: true` on a relation whose verb is the static default and not `calls` (§5).
- `resources` (PR 5): one entry per resource node — `key`, `kind`, `name`, `declared_by` (the
  files), `home` (a component id, or none for a shared resource at the root), `children` (`key`,
  `kind`, `name`, `owner`).
- `files` entries for artifact files that anchor an edge, so file coverage counts them as analysed.

The vscode webview draws a resource node in an outside style (dashed, as the rulers draw them)
and shows a badge's children when its owner expands. The wrapper keeps resource edges in commit
diffs; today its method-level differ drops an edge whose end is not a file.

**Debug dumps** — what the evals checks grade before any arrow exists (`CODEBOARDING_WIRING_DUMP=<dir>`):

```json
// units.json
{"repo": "owner/name", "commit": "<sha>",
 "units": [{"id": "<stable id>", "dir": "<repo-relative directory>",
            "kind": "csproj|maven|npm|python|go|compose|k8s|skaffold|aspire|other",
            "manifest": "<repo-relative path>", "aliases": ["<name>", "..."],
            "builds": ["<repo-relative path of a Dockerfile or manifest that builds it>"]}],
 "diagnostics": [{"code": "ambiguous_alias|unit_without_manifest|unreadable_manifest|ignored_manifest",
                  "message": "...", "paths": ["..."]}]}
```

```json
// anchors.json
{"repo": "owner/name", "commit": "<sha>",
 "anchors": [{"family": "A1|A2|A3|C1|C2|C3|C4|D1|E2|F3", "role": "def|use",
              "key": "<as written>", "norm_key": "<normalised>",
              "file": "<repo-relative path>", "line": 12, "unit": "<unit id or null>", "tier": "T1|T2|T3"}]}
```

`diagnostics.json` lists every unresolved use, every unused definition, every ambiguous key and
every ignored file with the reason, in the same shape as the `diagnostics` list above.

## 9. Incremental

A changed compose, manifest or configuration file is filtered out of an incremental run today
because it has no language extension, so the run is a no-op. The pass instead reruns whole on any
change (it is sub-second), diffs its edge set against the baseline, and marks both endpoint scopes
of every changed edge as changed. `_validate_no_dangling_references` and `invalidate_files`
tolerate resource and file nodes. The e-series incremental fixtures gain wiring cases: change a
compose env value, rename a route; each has an expected relation delta and nothing else.

## 10. Measurement

Every PR is measured with no model call, in seconds, by loading the P0 pickles of the seven rulers
(`CodeBoarding-evals/runs/wiring-p0/artifacts/`) and running only the new pass on top; the static
phase is re-run once after the tag bump. The scripts are in `CodeBoarding-evals/tools/wiring-study/`
and the scorer in `codeboarding_evals.reference.arrows`.

| what | how | passes when |
|---|---|---|
| no change where none is claimed | fold the seven pickles with the base engine and the candidate, compare the documents | byte-identical |
| unit coverage (PR 2) | `unit_coverage.py` over `units.json` | every ruler module in exactly one unit, no unit spanning two boxes, no ambiguous alias |
| anchor recall (PR 3) | `anchor_recall.py` over `anchors.json` against the companions' evidence tables | at least 0.9 on the dev rulers, recorded on the hold-outs |
| arrows (PR 4, PR 6) | the arrow scorer, stated and stated-plus-derived, direction-aware and blind | eShop at or above 20 of 23; every PetClinic arrow anchored in YAML, compose or a pom; hold-outs recorded once at phase exit |
| partition guard | boxes on eShop, abp, modulify with the flag on and off | byte-identical partitions |
| negative set | `wiring_negative` on the nine release repositories other than eShop | no resource node, no wiring edge, tree specification unchanged |
| determinism | two runs, macOS and the action's Linux container | identical edge sets |
| cost | static-phase timing with the flag on and off | under 5 % |

Dev rulers, looked at while a rule is written: eShop, Spring PetClinic. Hold-outs, scored blind
once per phase exit: piggymetrics, Zulip, Bank of Anthos, pitstop, Nango. A rule that helps the dev
set and hurts a hold-out does not ship.

## 11. The stack

| PR | adds | on the diagram | proof |
|---|---|---|---|
| 1 kinds and plumbing | `EdgeKind` with verbs and the three policy sets, sites on reference edges, relations from non-call edges, `kind` and `default_label` in the document, tag v12, this page | nothing | byte-identical on the seven rulers; unit tests on the policy table |
| 2 scan and units | artifact discovery and the unit table, `units.json` | nothing | unit coverage on the seven rulers; zero code units from the negative set's compose files |
| 3 anchors | one reader per format, `anchors.json` | nothing | anchor recall |
| 4 joins and edges | the join rules, diagnostics, edges between code units drawn with verbs | new arrows between existing boxes | arrows between code boxes; partition guard; negative set; determinism |
| 5 resource nodes | discovery, kind catalogue, children, the `resources` section, not drawn | nothing | present with the right kind on the seven rulers; zero on the negative set |
| 6 arrows to resources and placement | unit-to-resource joins, home and level, drawn as outside nodes | resource nodes and their arrows | the P1 exit numbers |
| 7 incremental | rerun on any change, edge diff, scope marking | an edited compose file changes the diagram | the incremental fixtures |

Then vscode (outside style, badges) and the wrapper (resource edges in commit diffs), and a
one-line PR that flips the flag once the action path runs with it on.

## 12. Not in this design

- No affinity for any wiring kind; ownership edges may gain it in P3, behind the ladder gate,
  measured per kind.
- No proto contract nodes until a graded ruler needs one.
- No Go readers; the rulers are C#, Java, Python and TypeScript.
- No routes from code, no clients from code, no messaging from code: those are P2.

Open, decided before the PR that needs them: the display-name catalogue's home (PR 5); whether
`DEPENDS_ON` is drawn at depth 1 or only inside a frame (PR 4, measured on eShop's project
references).
