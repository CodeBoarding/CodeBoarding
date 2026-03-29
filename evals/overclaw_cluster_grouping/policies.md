# Agent Policy: Cluster Grouping (step_clusters_grouping)

## Purpose

Groups pre-computed CFG (Control Flow Graph) clusters into logical architectural
components. This is the first LLM step in the AbstractionAgent pipeline — its output
feeds directly into the final component architecture generation.

## Decision Rules

1. **Every cluster ID must be assigned** to exactly one component's `cluster_ids`.
   Missing clusters cause downstream file-assignment failures.
2. **No duplicate cluster IDs** across components. A cluster belongs to one and only
   one logical group.
3. **Component count should be 4–12** for a typical project. Fewer than 4 means the
   grouping is too coarse; more than 12 means it's too granular for the next step
   (final analysis) to synthesize effectively.
4. **Minimize singleton components** (components with only 1 cluster). Singletons
   indicate the LLM failed to find meaningful groupings. Acceptable only when a
   cluster is genuinely isolated.
5. **Group names must be descriptive** and reflect architectural purpose (e.g.,
   "Document Conversion Pipeline", "Plugin Registry"), not implementation details
   (e.g., "Cluster 3 and 5", "Misc Functions").
6. **Descriptions must explain WHY** clusters are grouped together, referencing
   call patterns, shared files, or domain cohesion — not just listing what's in them.
7. **Inter-group interactions** should be mentioned in descriptions when the
   inter-cluster connections data shows edges between the groups.

## Constraints

- The agent receives a formatted cluster string with node names, file paths, and
  inter-cluster connections. It must use ALL of this information.
- For large codebases (500+ clusters), only the top 55 clusters are shown
  (`MAX_DISPLAY_CLUSTERS = 55`). The agent must still assign ALL cluster IDs,
  including those not displayed in detail.
- The output must parse into a `ClusterAnalysis` Pydantic model with a list of
  `ClustersComponent` objects, each having `name`, `cluster_ids`, and `description`.
- The validation step (`validate_cluster_coverage`) will reject results with missing
  cluster IDs and trigger up to 3 retry rounds with feedback.

## Priority Order

1. **100% cluster coverage** — every expected cluster ID must appear exactly once
2. **No duplicates** — each cluster ID in exactly one component
3. **Reasonable component count** (4–12)
4. **Meaningful groupings** — semantically coherent, not random
5. **Descriptive names and descriptions** — useful for the next pipeline step

## Known Failure Modes (500+ clusters)

| Scenario | Root Cause | Expected Fix |
|----------|-----------|--------------|
| Missing 30–50% of cluster IDs | LLM loses track of high cluster IDs in long context | Chunk clusters or use structured enumeration |
| All clusters in 2–3 mega-groups | LLM over-generalizes to reduce output length | Prompt for finer granularity |
| Duplicate cluster IDs across groups | LLM copies IDs when correcting coverage | Validation feedback should be explicit about which IDs are duplicated |
| Hallucinated cluster IDs | LLM invents IDs not in the input | Post-processing filter (not currently implemented) |
| Singleton-heavy output | LLM assigns each cluster to its own group | Prompt should emphasize grouping rationale |

## Scoring Rubric

All dimensions scored 0–100, then combined via weighted average:

| Dimension | Weight | Source | Scoring |
|-----------|--------|--------|---------|
| Coverage | 50 | `validate_cluster_coverage` (production validator) | `assigned ∩ expected / expected * 100` |
| No Duplicates | 15 | Structural check | `100 - duplicate_count * 10` |
| No Hallucinated IDs | 10 | Structural check | `100 - hallucinated_count * 10` |
| Structural Quality | 10 | Predicts downstream validators | `100 - 20 per issue type` (empty components, weak descriptions, duplicate names) |
| Component Count | 5 | Range check | `100 if 4–12, else decays` |
| Grouping Quality | 10 | Singleton ratio | `100 - singleton_ratio * 100` |

## Edge Cases

| Scenario | Expected Behavior |
|----------|-------------------|
| Only 1 cluster total | Return 1 component with that cluster |
| 2–3 clusters | Return 1–2 components, grouping is minimal |
| 500+ clusters | Must still achieve 100% coverage; chunking strategy may be needed |
| Multi-language repo | Clusters from all languages must be assigned; language boundaries don't imply component boundaries |
| Clusters with no inter-connections | Group by file proximity or domain similarity |
