# cluster_viz — see every level of the clustering, and why it came out that way

A finished analysis knows far more about its own clustering than `analysis.json` shows.
Each method carries the scoped cluster id it was given at *every* level
(`CallGraph.method_cluster_paths`), and every component records the clusters it
claimed (`source_cluster_ids`). This package turns those artifacts into one JSON
payload plus a standalone HTML viewer, without re-running the pipeline or the LLM.

```bash
python -m cluster_viz --artifacts runs/cluster-viz/eshop --repo ~/repos/eShop
# writes <artifacts>/clustering.json and <artifacts>/clustering.html
```

Open `clustering.html` in a browser — everything is inlined, no server, no CDN.

## The model

A **scope** is one clustering run. The root scope (`""`) partitions every method in
the repo into leaf clusters `1, 2, 3…` and groups them into top-level components.
Each component that gets expanded re-clusters *its own* methods under its component
id, producing `1.4`, `1.1.3`, and so on. So a scoped cluster id is
`<owning component id>.<local cluster id>`, and its level is its segment count.

The containment chain that follows is what the viewer draws as nested circles:

```
component 1  ⊃  component 1.1  ⊃  component 1.1.2  ⊃  its finest leaf cluster  ⊃  methods
```

Two partitions of the same set exist at each level — the leaf clusters, and the
components those clusters were grouped into. The viewer colours by either.

## What the payload holds

| key | what it is |
| --- | --- |
| `nodes` | every call-graph method: name, file, lines, kind, and `path` — its cluster id at each level |
| `edges` | call edges plus the reference edges (contains/inherits/typeref/import) clustering runs on |
| `components` | the flattened component tree with the clusters each one claimed |
| `scopes[]` | one entry per clustering run: its clusters, the meta-graph between them, the grouping, and the decision trace |
| `layout` | precomputed positions — one point per method, one circle per component and leaf cluster |
| `levels` | how many clusters, components and scopes exist at each level |

### The decision trace

`supercluster_by_modularity_peak` only returns its winning partition, so the reasoning
is not recoverable from the artifacts. Everything it does is deterministic (fixed seed,
fixed resolution ladder), so `cluster_viz.decision` re-runs it with the intermediate
steps recorded, and checks the replay against the real entry point — a drifting mirror
shows up as `matches_pipeline: false` rather than as a plausible-looking lie.

Per scope you get:

- **`sweep`** — every rung of the resolution ladder, its community count and modularity,
  and which one won. This is where the *number* of components comes from: the highest
  modularity among partitions whose non-singleton count lands in 5–8 (3–8 below the top
  level). The global modularity peak often sits outside that window; the trace shows what
  the range constraint cost.
- **`seeds` / `promoted`** — the communities Leiden resolved directly, plus any leftover
  promoted to its own component to reach the floor.
- **`absorptions`** — where every leftover cluster went and why: `hops` (nearest seed in
  the cluster graph), `package` (no path at all, placed by shared directory prefix), or
  `smallest_group` (neither).
- **`shipped_matches_replay`** — whether the grouping in `analysis.json` is the one a
  from-scratch run produces. False is expected after an incremental run, which carries
  the previous grouping forward instead of re-deriving it.

Cohesion numbers on clusters and components are call-weighted: internal edge weight over
total incident edge weight.

## Reading the viewer

- **Map** — one dot per method, positioned by its place in the hierarchy. The level
  slider re-colours by the cluster or component at that level; circles show one ring per
  level down to the selected one. Click a dot to open its component; hover for its full
  lineage.
- **Scope** — one clustering run: leaf clusters as bubbles sized by method count,
  positioned by the meta-graph that modularity was scored on, edge width by weight,
  colour by owning component. Faded bubbles were absorbed rather than resolved as seeds.
- The right-hand panel answers "why is this here" for whatever is selected — cluster,
  component, or the grouping decision itself.

## Caveats

- The trace is a faithful *replay*, not a recording. It matches the pipeline on a full
  run; where it cannot, the payload says so in `meta.warnings`.
- Methods with no recorded lineage (never clustered) are drawn outside every component
  and counted in `meta.warnings`.
- Sub-scope clustering uses the component's own subgraph, reconstructed here as the
  induced subgraph of the repo-wide clustering graph.
