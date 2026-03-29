# Agent Policy: Agent

## 1. Domain Knowledge

### 1.1 Purpose & Context
This agent evaluates the cluster-grouping stage of architecture abstraction by replaying precomputed repository snapshots and asking the LLM to group CFG clusters into logical components. The grouping output is judged primarily on architectural usefulness and exact cluster-ID accounting.

### 1.2 Domain Rules
1. Every cluster ID must be assigned to exactly one component’s `cluster_ids`.
2. No duplicate cluster IDs across components; a cluster belongs to one logical group only.
3. Component count should usually be 4–12 for typical projects; fewer is too coarse, more is too granular.
4. Minimize singleton components; use them only when a cluster is genuinely isolated.
5. Group names must be descriptive and architecture-oriented, not implementation-like or placeholder labels.
6. Descriptions must explain why clusters are grouped together, using call patterns, shared files, or domain cohesion.
7. Inter-group interactions should be mentioned when inter-cluster connection data indicates edges between groups.
8. (added) Groupings may cross language boundaries in multi-language repositories; language alone is not a required boundary.
9. (added) The agent should prefer semantically cohesive multi-cluster groups over coverage-only packing.

### 1.3 Domain Edge Cases
- Only 1 cluster total → return 1 component containing that cluster.
- 2–3 clusters → 1–2 components is acceptable; the 4–12 rule is not mandatory for tiny projects.
- 500+ clusters → all expected IDs must still be assigned even if only top clusters are displayed in detail.
- Multi-language repo → assign all cluster IDs across languages.
- Clusters with weak/no interconnections → group by file proximity, naming, or domain similarity.
- (added) Empty output or components with empty `cluster_ids` are structurally invalid and heavily penalized.
- (added) Hallucinated cluster IDs must never be introduced.

### 1.4 Terminology & Definitions
- **Cluster**: a precomputed CFG cluster of related methods/functions.
- **Component**: an LLM-defined logical grouping of one or more cluster IDs.
- **Singleton**: a component containing exactly one cluster ID.
- **Coverage**: percent of expected cluster IDs assigned at least once.
- **Hallucinated ID**: a cluster ID not present in the snapshot’s expected set.

## 2. Agent Behavior

### 2.1 Output Constraints
- Output must parse as `ClusterAnalysis` with `cluster_components: list[ClustersComponent]`.
- Each `ClustersComponent` must contain `name: str`, `cluster_ids: list[int]`, and `description: str`.
- (added) Descriptions shorter than ~20 stripped characters are treated as weak.
- (added) Score dimensions are 0–100: coverage, duplicates, hallucinated, structural, count, grouping, total.
- (added) Final eval output contains `per_project`, `grouped_score`, and `project_count`; `grouped_score` is the sum of per-project `total_score`, not an average.

### 2.2 Tool Usage
- Snapshot loading must occur before grouping and scoring.
- LLMs are initialized before invoking `AbstractionAgent.step_clusters_grouping`.
- `validate_cluster_coverage` must run before/alongside scoring to measure missing IDs.
- (added) In this eval harness, retry correction is disabled: `max_validation_retries=0`, so the first model output is scored directly.
- (added) Snapshot inputs are trusted pickle files; no schema/version safety is enforced by the harness.

### 2.3 Decision Mapping
- Use snapshot `cluster_results` as the sole cluster-ID source of truth.
- Compute missing, duplicate, and hallucinated IDs from produced `cluster_ids`.
- Prefer 4–12 components when feasible; for tiny repos allow fewer.
- Favor lower singleton ratio to improve grouping quality.
- Structural quality decreases for empty components, duplicate normalized names, and weak descriptions.

### 2.4 Quality Expectations
- Highest priority: 100% cluster coverage.
- Next: zero duplicate IDs and zero hallucinated IDs.
- Then: reasonable component count and meaningful multi-cluster grouping.
- Then: descriptive names and explanations that justify grouping decisions and mention interactions where supported.
