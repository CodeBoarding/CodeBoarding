# simplify-incremental-analysis: Architecture Summary

**Branch**: `simplify-incremental-analysis` (one squashed commit + uncommitted improvements)

**Net delta**: ~2,100 lines added, ~9,500 removed (82 files committed). The old 7-file incremental pipeline (~2,700 lines) was replaced by 3 modules (~700 lines) + ~600 lines of stitching helpers.

## What was removed (old approach)

| Deleted file | Lines | Purpose |
|---|---|---|
| `incremental_tracer.py` | 969 | Diff parsing, method-level tracing across commits |
| `incremental_updater.py` | 577 | Per-method status computation & merge |
| `scope_planner.py` | 252 | Deciding which files/components to re-analyze |
| `incremental_pipeline.py` | 285 | Orchestrating the old multi-step flow |
| `incremental_models.py` | 230 | Pydantic models for method diffs |
| `incremental_payload.py` | 129 | Serialising diff payload |
| `analysis_patcher.py` | 230 | Patching analysis JSON from method diffs |

**Old flow**: Parse git diff → identify changed methods → plan scope → ask LLM to re-group changed methods → patch JSON. Multiple LLM calls, method-level granularity, heavy state.

## New approach: cluster-driven incremental

Operates at **cluster granularity** (Louvain communities in the CFG) with exactly **one LLM call** per run.

### Pipeline step-by-step

```
Wrapper session.run_incremental
  └─ SnapshotWorktreeManager: probe tree (Unchanged/NoPrior/Changed)
  └─ AnalysisController.run_incremental(snapshot_worktree, output_dir)
      └─ Core: run_incremental() → DiagramGenerator.generate_analysis_incremental()
          ├── 1. scrub_deleted_files()          [deterministic]
          ├── 2. snapshot_from_analysis()        [deterministic]
          ├── 3. compute_cluster_delta(changes?) [deterministic, diff-scoped]
          ├── 4. IncrementalAgent.step_group_delta()  [ONE LLM CALL]
          ├── 5. stitch_delta()                  [deterministic]
          ├── 6. repopulate_touched_scopes()     [deterministic]
          ├── 7. prune_empty_components()        [deterministic]
          ├── 8. _generate_subcomponents()       [LLM: re-detail affected]
          └── 9. save_analysis()
```

### Step 1: `scrub_deleted_files()` — no LLM
**`agents/incremental_agent.py:519`** — Removes `file_methods` groups and `key_entities` referencing files no longer on disk. Runs BEFORE any cluster math because deleted-file scenarios don't always surface as cluster-id changes: orphan-routed files were never in any cluster, so the cluster pipeline alone can't detect them.

### Step 2: `snapshot_from_analysis()` — no LLM
**`diagram_analysis/cluster_snapshot.py:51`** — Reconstructs prior clustering from `analysis.json`. Each `Component` carries `cluster_members: dict[int, list[str]]` (persisted in JSON, excluded from LLM output). Walks all components, collects per-cluster member sets, partitions by language. **No sidecar file** — `analysis.json` is the single source of truth.

Language resolution uses two signals: (1) the fresh CFG if the qname still exists, (2) the file extension from prior `file_methods` for deleted/drifted qnames. This ensures `removed_nodes` is correctly computed in the delta step — deleted qnames are kept in the snapshot, not silently dropped.

### Step 3: `compute_cluster_delta()` — no LLM, diff-scoped
**`diagram_analysis/cluster_delta.py:78`** — Two flavors, plus optional diff scoping.

**Diff scoping** (new, when `ChangeSet` is provided): Before choosing a flavor, qnames are filtered through a four-quadrant model based on (in prior analysis?, in source diff?):

| Quadrant | Prior analysis | Source diff | Action |
|---|---|---|---|
| Tracked change | ✓ | ✓ | Keep — normal delta |
| Inconsistent | ✓ | ✗ | Keep — logged as WARNING (qname vanished without its file changing) |
| Genuine new | ✗ | ✓ | Keep — real addition in changed file |
| Drift | ✗ | ✗ | **Drop** — noise from unchanged files |

When `changes=None` (e.g., GitHub Action without a diff source), no scoping is applied — backward compatible.

**Flavor B** (default, scoped change < 25%):
The seeded/iterative approach. Old cluster member sets are loaded and mutated:
1. **Remove deleted nodes** from old clusters
2. **Route added nodes** to the cluster they share the most CFG edges with (`_argmax_neighbor_cluster`, tie-break by file co-location)
3. **Louvain on leftovers**: added nodes that don't fit any existing cluster are grouped via `louvain_communities` into brand-new clusters
4. Produces: `new_cluster_ids`, `changed_cluster_ids`, `dropped_cluster_ids`

Existing cluster IDs are **preserved** (stable). New clusters get fresh IDs. This is not "load + append" — it's load → remove deleted → route new by graph affinity → Louvain the rest.

**Flavor A** (fallback, change >= 25%): Fresh Louvain on full CFG, match to old clusters by greedy 1:1 Jaccard >= 0.5.

### Step 4: `IncrementalAgent.step_group_delta()` — **THE LLM CALL**
**`agents/incremental_agent.py:78`**, prompt in `agents/prompts/incremental_grouping.py`

**What the LLM receives**:
1. **Project context**: meta-analysis (project type, description)
2. **Existing components** (two-tier rendering):
   - *Affected* (full: id, name, description) — components whose files overlap with changed clusters
   - *Other* (name only: `id "name"`) — valid routing targets, descriptions omitted to save tokens
3. **Cluster groups to assign**: Delta's `new + changed` clusters rendered as CFG cluster strings (same format as full-analysis grouping prompt)

**What the LLM is prompted to do**:
> "For each cluster id, decide which component it belongs to: (1) assign to existing component by reusing its exact name, or (2) create a new component with name, description, and parent_id."

The prompt includes a **tool usage policy**: keep reads small and targeted (single representative qname per cluster), stop as soon as confidence is high.

**Tool access**: `read_source_reference` only — the full ReAct toolkit is NOT attached. This constrains the agent from speculatively reading large code blocks.

**Fast-path parse**: `_direct_pydantic_parse` in `agent.py` tries to parse the LLM response directly as the target Pydantic model before invoking the trustcall extractor — saves a second LLM round-trip when the agent emits clean JSON.

**Validation**: Up to 3 retries via `validate_cluster_coverage` if the LLM doesn't cover all cluster ids.

**Output**: `ClusterAnalysis` with `cluster_components` — each has `name`, `cluster_ids`, `description`, `parent_id`. Every affected cluster id must appear exactly once.

### Step 5: `stitch_delta()` — no LLM
**`agents/incremental_agent.py:201`** — Applies routing decisions:
1. Remap cluster IDs, drop removed clusters across every existing component
2. Merge new cluster ids into existing components (by case-insensitive name match), or create new ones under the requested `parent_id`
3. Assign hierarchical IDs (`only_new=True` preserves existing component IDs)
4. Returns `redetail_ids` — components whose clusters changed and need re-detailing

### Step 6: `repopulate_touched_scopes()` — no LLM
**`agents/incremental_agent.py:339`** — Per-component rebuild of `file_methods` from live cluster results. Siblings whose clusters didn't change keep their existing `file_methods` untouched. File paths are normalized to repo-relative posix form via `normalize_repo_path` (shared helper in `io_utils.py`). Re-runs `build_static_relations` at scope level after refresh.

### Step 7: `prune_empty_components()` — no LLM
**`agents/incremental_agent.py:572`** — Removes components with no methods left after scrub + repopulation. Cascades: sub-analyses hanging off pruned components are deleted; relations referencing removed components are stripped.

### Step 8: `_generate_subcomponents()` — standard LLM re-detailing
Same frontier queue as full analysis, seeded only with `redetail_ids` components. Each goes through `DetailsAgent.run()`. Newly expandable children are processed recursively up to `depth_level`.

## Key data model changes

- `Component`: added `cluster_members: dict[int, list[str]]` (excluded from LLM, persisted in JSON — the inline snapshot for incremental baseline)
- `ClustersComponent`: added `parent_id` for new component placement during incremental routing
- `ClusterSnapshotEntry`: added `member_files: dict[str, str]` — per-qname file paths for diff scoping
- `assign_component_ids()`: new `only_new=True` mode preserves existing IDs
- `CodeBoardingAgent`: new `tool_names` parameter and `_direct_pydantic_parse` fast-path
- `normalize_repo_path()`: extracted to `diagram_analysis/io_utils.py` as shared utility

## How `scrub_deleted_files` and diff scoping complement each other

| Mechanism | Scope | What it catches |
|---|---|---|
| `scrub_deleted_files` (Step 1) | `file_methods` / `key_entities` | Files physically deleted from disk — including orphan-routed files never in any cluster |
| Diff scoping in `compute_cluster_delta` (Step 3) | Cluster member qnames | Drift noise from qnames shifting in unchanged files |

Both are needed. Step 1 handles the layer the cluster pipeline can't see: a component's `file_methods` may reference files that were never clustered (orphan-routed to fallback components). Step 3's diff scoping prevents the LLM from being invoked on spurious cluster changes caused by CFG drift in unchanged files.

## Old vs new comparison

| Aspect | Old | New |
|---|---|---|
| Granularity | Method-level diff | Cluster-level (Louvain) |
| LLM calls | Multiple | One + standard re-detailing |
| Baseline | Sidecar files, git diff parsing | Inline `cluster_members` on Component |
| New clusters | Not supported cleanly | Seeded Louvain on unassigned nodes |
| Fallback | Manual scope expansion | Auto Flavor A when change > 25% |
| Provider prompts | Per-provider variants | Single provider-agnostic template |
| Drift handling | None | Diff scoping (4-quadrant filter) |
| Agent toolkit | Full ReAct kit | Constrained to `read_source_reference` |
| Response parsing | Always trustcall extractor | Direct Pydantic parse fast-path |
| Path normalization | Inline in DiagramGenerator | Shared `normalize_repo_path` utility |
| Pipeline LOC | ~2,700 | ~1,300 |
