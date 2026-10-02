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
  action's Linux container, with no toolchain installed, and in two processes with different hash
  seeds: every list the pass writes is sorted on a complete key, and a test runs the pass under
  `PYTHONHASHSEED=1` and `=7` and diffs the dumps.
- **It can never break an analysis.** `StaticAnalyzer.analyze` runs it through `run_or_report`:
  whatever it raises becomes one logged `unreadable_manifest` diagnostic and an empty result. Inside,
  a manifest is read through `mapping()` and `listing()` — a scalar where a mapping was expected, a
  `services:` written as a list, a service with no name — so a mistake in a file is a row, never a
  crash. The repository's own name comes from its git remote (`owner/name`), never from the
  checkout directory, which is whatever the machine called it.
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
| family | which kind of file a key lives in: `build_manifest`, `deployment`, `configuration`, `service_names`, `http_in_code`, `messaging`, `generated`, `data_access`. The research appendices number these A1, A2, C1, C2, C3, C4/D1, E2 and F3; the code and the dumps spell them out, and the evals graders read both spellings through one alias map |
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
  layout (v12), and a pickle written before the bucket existed loads as a run with no wiring. The
  types are `static_analyzer/wiring_results.py`, beside the other buckets, so the results object
  carries them without importing the pass; the pass is `static_analyzer/wiring/`, one module per
  question: `scan` what may be read, `images` what this repository builds, `compose` what a compose
  project says, `manifests` and `topology` what declares a unit, `units` the table, `__init__` the
  entry point and the dumps.
- **Files it reads:** an allowlist, never source. Build manifests (`*.csproj`, `*.sln`, `pom.xml`,
  `build.gradle`, `package.json`, `pnpm-workspace.yaml`, `pyproject.toml`, `setup.py`,
  `requirements*.txt`, `go.mod`, `Cargo.toml`, `composer.json`, `Gemfile`, `*.gemspec`, `mix.exs`),
  deployment (`docker-compose*.y*ml`, `compose*.y*ml`, Kubernetes manifests, `skaffold*.y*ml`,
  Helm charts, Dockerfiles, an Aspire AppHost's own `.cs`, `.github/workflows/*.y*ml`),
  configuration (`.env*`, `appsettings*.json`, `application*.y*ml`, `bootstrap*.y*ml`, the YAML a
  unit keeps under `config/` or names as its settings, a configuration server's shared directory),
  nginx configuration. A `.env.example`, `.env.sample`, `.env.template` or `.env.dist` documents
  what a deployment may set and sets nothing; a CI file, a tool's own YAML and a hidden file are
  not configuration; a `.properties` that is not Spring's own configuration is read for values
  shaped like a host or a connection only, because a message bundle's `label.server=Server` is a
  label. A file the allowlist does not name is never opened, and nothing over 2 MB is read.
  A configuration file states a handful of facts, so one over 128 KB is a catalogue — a provider
  list, an API specification, a generated schema — and is data rather than configuration, in
  whatever notation it is written: it declares no wiring and costs more to read than everything
  that does.
- **Source, for two readers only:** a literal service name (`http://vets-service`, `lb://`, a
  `@FeignClient`, a discovery lookup, a registry annotation) and an environment read (`os.environ`,
  `process.env`, `Configuration["…"]`, `System.getenv`, `@Value("${…}")`), together with the client
  type that says what a unit talks to (`VectorStore`, `MongoTemplate`). Structure in source is the
  language servers' work and this pass never touches it; a `*.config.*` file is tooling rather than
  service code, and is skipped. The readers see source with its asides blanked once, before any of
  them looks: a docstring, a `/* */` block, a `#` line and a `//` comment (where a comment can
  start, never inside `http://`) are not declarations, so a commented-out `// "http://old-service"`
  draws nothing. A reader names the standard library's and the frameworks' environment readers; a
  project's own wrapper (`mustMapEnv`, `GetRequiredValue`) is that project's, and no rule names
  one repository's helper. A file whose name says a tool wrote it — `*.designer.*`, `*.g.*`,
  `*.generated.*`, a model snapshot, a compiled proto, a lockfile — is skipped wherever it sits,
  as source and as configuration: nothing in it is a decision anyone made.
- **What it will not read:** `node_modules`, `vendor`, build output (`bin`, `obj`, `dist`, `out`,
  `target`), every hidden directory but `.github/workflows`, a test directory, a file named as a
  test, and a project template (a tree holding `.template.config`, `cookiecutter.json`, or a `{{ }}`
  in a path). A test directory is one named as such (`test`, `tests`, `__tests__`, `spec`, `e2e`,
  `testdata`, `fixtures`, `mocks`, `testing`) or named for what it tests with a plural suffix in
  either spelling (`ui-tests`, `Basket.FunctionalTests`, `Acme.Tests`); a singular suffix is a name
  (`ABTest`, `plugin-chart-paired-t-test`), because losing a unit costs a box while keeping one
  costs an entry nothing joins. `.gitignore` decides what is in the tree at all, applied to
  directories during the walk and to files, so a developer's untracked `.env` cannot make a local
  run disagree with the same commit in CI. `.codeboardingignore` excludes the directories its user
  excluded; its file patterns do not hide a manifest the allowlist names, which is how the pass
  reads the dotfiles and `*.config.*` files the template ignores. Each of these is a diagnostic
  row — one per excluded place, not one per file — and so is every other directory the walk leaves
  out, except the repository's own `.git`. A Dockerfile a manifest names by path is resolved on the
  filesystem even under a directory the walk left out (`build/package/Dockerfile`, the Go standard
  layout), because a build that names a file means that file.
- **Cost:** the pass stays under 5 % of the static phase of the same repository, and that is the
  whole acceptance rule. The P0 probes take 0.1–8 s; those timings say how fast the readers are,
  not what the pass may spend.

## 4. Nodes

| node | key | where it sits |
|---|---|---|
| code symbol | as today, `file\|qualified name` | its component |
| artifact file | a node of type FILE whose qualified name is its repository-relative path — a unit's own manifest or configuration file, which is where an edge that starts or ends at that unit lands | the component that owns its directory |
| resource | `resource:<kind>:<name>`, and `resource:<kind>:<name>/<kind>:<child>` for a database on a server or a route on a gateway | a resource node is never a code component and never counts toward the component budget; where it is drawn is §7 |

A resource's `name` is what the repository declares for it — the compose service, the Kubernetes
workload, the Aspire resource — never the image. The image decides the kind.

**A shared file is a site, not an endpoint.** The arrow a root compose file, an Aspire AppHost or a
Kubernetes manifest directory declares joins the two units it names; the file and the line are the
edge's site. Such a file belongs to no box, and it counts as analysed. A file inside a unit's
directory — its own manifest, `application.yml`, `appsettings.json`, its Dockerfile — belongs to
that unit's component like every other file there, and is the unit's endpoint whenever the anchor
is not in code; where the anchor is a literal in code, the endpoint is the enclosing code symbol. A
directory that builds or deploys something is a unit and can be a box: Bank of Anthos draws its two
database directories that way.

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
  In `analysis.json` the flag is written only where the wording would tell a reader the wrong
  thing — a default verb that is not `calls`, and a `calls` someone authored — so documents with
  only calls in them do not change. A document without the flag is judged by its wording: `calls`
  is the static default, any other verb was written.
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
   edge: an nginx `proxy_pass http://$backend` names whatever the variable holds, and the anchor
   says so with its tier.
5. **Record which families found each edge.** Two independent mechanisms per arrow is the
   confidence signal, and attribution is what the scorer reports.
6. **Environment variants are unioned, not chosen.** Edges from `appsettings.Production.json`,
   `.env.production`, a Kustomize overlay or a compose override carry the environments they hold
   in. The compose files in one directory are one project, merged by service name the way compose
   merges a file and its override — a later file wins a scalar, names accumulate — so a service is
   one unit whether its image and its build context come from one file or two. `${VAR}` and
   `${VAR:-default}` interpolate from the `.env` beside the file and from nothing else: this
   machine's environment would make a local run disagree with the same commit in CI. `env_file`
   fills a service's environment, which the compose specification does not interpolate from.
   `extends` is followed, `include` brings another file's services in, YAML anchors and merge keys
   are ordinary YAML, and a service with `profiles` is a variant — recorded in the unit table with
   its profiles, and not drawn.
7. **Ambiguity produces no edge.** One key with several providers emits nothing and reports both
   candidates.

Two more, from the P0 ceilings:

8. **Paths are canonical.** A unit directory is spelled from the repository root however the
   manifest reached it (`context: ../../../` plus `project: src/ledger/x` is `src/ledger/x`).
9. **A host resolves to a unit only when it is a name the topology declares.** A public FQDN in a
   documentation URL never falls back to its first DNS label, and its `?host=&database=` query
   names no store: the `Key=Value;` parts of a connection string are read under a connection key
   or in a value with no scheme. Only a network scheme carries a host:
   a deep link (`maui://`, `vscode://`) names a callback a device answers, not a service anything
   reaches. A value naming the machine itself (`localhost`, `127.0.0.1`) names no unit and no
   resource, wherever it is written, including inside a connection string.

**What declares a unit.** A build manifest declares the directory it sits in, except where it
builds nothing itself: a Maven aggregator (`packaging=pom`), a Cargo workspace root, a
`package.json` with no name. A `requirements.txt` declares its directory when a `.py` sits anywhere
below it or a Dockerfile beside it installs it; one at the root of a docs tree declares nothing. An npm package is a unit when a workspace lists it or it is the
repository's own root package, and a workspace glob is a glob — a package nested inside a member is
not itself a member. A deployment file declares the directory it builds, which is the Dockerfile's
own directory whenever the Dockerfile sits inside the build context: a context is what is sent to
the daemon and is often the whole tree, while the Dockerfile sits with the thing it builds.

**What declares nothing.** A build whose Dockerfile or context is not in the repository — a
template's, a stale path. A build whose Dockerfile copies nothing from its context: it brings no
file of this repository into the image, so it is a toolchain or dev-container image rather than the
product (a `RUN --mount=type=bind` without `from=` reads the context and counts as copying it). A
build whose Dockerfile copies only configuration — a Prometheus with its scrape list, a Grafana
with its dashboards — customises the image it starts `FROM` and builds no code: it is reported as
`configured_image`, and it is a resource of that image's kind rather than a unit (§7). A compose project in which no service is built from the repository and none runs an image
this repository builds — per-test infrastructure, dev containers, CI build images — which is
ignored with `ignored_manifest` and its reason. A deployment file naming the repository root, unless
a manifest there builds it: otherwise a CI image that mounts the tree reads as the product. Test
directories, files named as tests (`*.test.*`, `*.spec.*`, `*_test.*`, `*Tests/`) and project
templates anchor nothing.

**Which directory an image is.** An image belongs to a unit when something here builds and tags it:
a compose service with both `build` and `image`, a skaffold artifact, a Maven plugin's tag
(resolved per module, since a parent declares the plugin once and `${project.artifactId}` differs
in each), or a CI workflow's build step — its `tags`, or the `name=` of a buildx output, with
`matrix` and the workflow's own `env` substituted. `vars` and `secrets` are not written down
anywhere, so an expression falls through them to whatever literal its `||` offers. A repository and
tag match first and the repository alone second, because a workflow tags `:latest` what a compose
file pins at `:2.1`. Failing all of that, an image whose last path segment is the repository's own
name is the repository root. An image name is a unit's alias only where one directory builds it.

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
- **Level.** A shared resource is a peer where its users meet. A level draws at most 15 nodes, and
  code boxes, resource nodes, grouped nodes and actors all count toward it. Over the cap, fold in
  this order: private resources into their owner (a badge, shown when the owner expands); registry,
  configuration and telemetry resources into one Infrastructure node that expands; remaining third
  parties into one External services node; and, if resources still take more than half the level,
  shared data stores into one Data stores node. The clustering's limit for that level then becomes
  15 minus the resource nodes that remain, and the level is clustered once more — one pass
  suffices, because merging code boxes can only turn a shared resource private. This is the only
  way wiring changes a partition, and the guard reads: boxes identical wherever a level is under
  the cap. On PetClinic this renders 13 nodes with three badges where the picture draws 16 peers;
  the scorer accepts both.
- **Infrastructure** flows (`REGISTERS_WITH`, `FETCHES_CONFIG`, `REPORTS_TO`) are drawn
  de-emphasised and collapsible: a star of such arrows says the same thing about every service.

## 8. Output contract

`analysis.json` gains, additively:

- `kind` on relation edges when it is not `call` (§5).
- `default_label` where the verb alone reads wrong: `true` for a static default that is not
  `calls`, `false` for a `calls` someone wrote (§5).
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
 "units": [{"id": "<the unit's directory, which is what makes it stable>",
            "dir": "<repo-relative directory, `.` for the repository itself>",
            "kind": "csproj|maven|gradle|go|rust|python|npm|ruby|php|elixir|compose|skaffold|aspire|k8s|helm|docker|other",
            "manifest": "<repo-relative path>", "aliases": ["<name>", "..."],
            "builds": ["<repo-relative path of a Dockerfile, manifest or deployment file that builds it>"],
            "variant": ["<a compose profile it only runs under>"]}],
 "diagnostics": [{"code": "ambiguous_alias|ambiguous_image|configured_image|ignored_manifest|unit_without_manifest|unreadable_manifest|unresolved_image",
                  "message": "...", "paths": ["..."]}]}
```

```json
// anchors.json
{"repo": "owner/name", "commit": "<sha>",
 "anchors": [{"family": "build_manifest|deployment|configuration|service_names|http_in_code|messaging|generated|data_access",
              "role": "def|use", "key": "<as written>", "norm_key": "<normalised>",
              "file": "<repo-relative path>", "line": 12, "column": 1,
              "unit": "<the unit it is about>", "tier": "T1|T2|T3",
              "setting": "<the configuration key a host was read under, else empty>"}]}
```

An anchor's `unit` is the unit it is **about**, not the unit whose file it is: a root compose file
belongs to no box, and the variable it sets belongs to the service it sets it on. Two rules follow
from what the file formats say. Inside a unit that declares `@EnableConfigServer`, or whose
`spring.cloud.config.server.native.search-locations` names the directory, `<name>.yml` (and
`<name>-<profile>.yml`) is about `Names.unit_of(name)` when that resolves to another unit, and
`application*.yml` is about every unit that fetches configuration from that server — never about
the server, which ships those settings and reads none of them; a shared file naming no unit is
reported. A Kubernetes ConfigMap or Secret is about each workload that consumes it: through
`envFrom`, every key of it; through `valueFrom`, the one key the container's variable is bound
to; a map nobody pulls in stays where it is written. A host an anchor names carries the
configuration key it was read under in `setting` (`spring.config.import`, `CONFIG_SERVER_URL`,
`depends_on`), which is what says what the connection is for. A role marker
(`@EnableEurekaServer`, `@EnableDiscoveryClient`) is normalised to `role:<annotation>`, a form no
unit's name can take. A schema declaration is DDL (`.sql`, `.ddl`) or the files of a migrations
directory; a `db/` package of source is code that talks to a database, not what it holds. Its `norm_key` is
normalised the way its family is compared — an environment or configuration key the way Spring's
relaxed binding and ASP.NET's `A__B` rule compare it (`spring.datasource.url` is
`SPRINGDATASOURCEURL`), a name the way the unit table spells the names a unit answers to. A route
is the one T2 anchor: its path is a template with its parameters collapsed. Its `line` is where the
setting is written, which is where its own key and its own value meet: a key repeated across
documents and a value repeated across settings each name the wrong line on their own.

`diagnostics.json` lists every unresolved use, every unused definition, every ambiguous key and
every ignored place with the reason, in the same shape as the `diagnostics` list above. It is also
the shape a resolver with a model in it would read one day — for an unresolved use, the anchor's
file, line and key, the lines around it, and the unit names in play — and nothing in P1 calls one.

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
| cost | static-phase timing with the flag on and off, per repository | under 5 % of that repository's static phase |

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
- No reader reads Go, Rust, Ruby, PHP or Elixir *source for structure*; their manifests are units
  like any other, because a directory that builds is a directory that builds, and the two source
  readers of §3 read a literal name and an environment read in every language the engines support.
  The rulers are C#, Java, Python and TypeScript.
- No resolver with a model in it. P1 joins what the files say and reports what it could not.
- No routes from code, no clients from code, no messaging from code: those are P2.

Open, decided before the PR that needs them: the display-name catalogue's home (PR 5); whether
`DEPENDS_ON` is drawn at depth 1 or only inside a frame (PR 4, measured on eShop's project
references).
