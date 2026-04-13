# DetailsAgent -- Technical Spec

| Field | Value |
|-------|-------|
| **Class** | `DetailsAgent` |
| **File** | `agents/details_agent.py` |
| **Inheritance** | `ClusterMethodsMixin, CodeBoardingAgent` |
| **LLM Steps** | 2 (`step_clusters_grouping`, `step_final_analysis`) |
| **Input** | A single `Component` object (not the full project). The agent operates on a **subgraph** scoped to that component's files. |
| **Entry point** | `run(component: Component)` returns `(AnalysisInsights, dict[str, ClusterResult])` |

---

## System Prompt

**Source**: `agents/prompts/claude_prompts.py:254-269` (constant `SYSTEM_DETAILS_MESSAGE`), surfaced via `get_system_details_message()`.

The system prompt is a **template** with variables `{project_name}`, `{meta_context}`, and `{project_type}`. It anchors the LLM to a single subsystem:

```text
You are a software architecture expert analyzing a subsystem of `{project_name}`.

Project Context:
{meta_context}

Instructions:
1. Start with available project context and CFG data
2. Use getClassHierarchy only for the target subsystem

Required outputs:
- Subsystem boundaries from context
- Central components (max 10) following {project_type} patterns
- Component responsibilities and interactions
- Internal subsystem relationships

Focus on subsystem-specific functionality. Avoid cross-cutting concerns like logging or error handling.
```

The anchoring instruction is implicit in the phrase "analyzing a **subsystem**" combined with "Focus on subsystem-specific functionality." The component scope is enforced by the fact that only the subgraph CFG data (filtered to the component's files) is fed into the user prompt, not the full project CFG.

---

## User Prompt (templates)

### step_clusters_grouping (details mode)

**Source**: `agents/prompts/claude_prompts.py:271-304` (constant `CFG_DETAILS_MESSAGE`).

**Template variables**: `{component}`, `{project_name}`, `{meta_context}`, `{project_type}`, `{cfg_clusters}`.

The `{component}` variable is filled with `component.llm_str()` and `{cfg_clusters}` is built by `_build_cluster_string()` over the subgraph cluster results only.

```text
Analyze and GROUP the Control Flow Graph clusters for the `{component}` subsystem of `{project_name}`.

Project Context:
{meta_context}

Project Type: {project_type}

The CFG has been pre-clustered into groups of related methods/functions. Each cluster represents methods that call each other frequently.

CFG Clusters:
{cfg_clusters}

Your Task:
GROUP similar clusters together into logical sub-components based on their relationships and purpose within this subsystem.

Instructions:
1. Analyze the clusters shown above and identify which ones work together or are functionally related
2. Group related clusters into meaningful sub-components
3. A sub-component can contain one or more cluster IDs (e.g., [1], [2, 5], or [3, 7, 9])
4. For each grouped sub-component, provide:
   - **name**: Short, descriptive name for this group
   - **cluster_ids**: List of cluster IDs that belong together
   - **description**: Comprehensive explanation including rationale and inter-group interactions

Focus on core subsystem functionality only. Avoid cross-cutting concerns like logging or error handling.

Output Format:
Return a ClusterAnalysis with cluster_components using ClustersComponent model.
```

**Synthetic example** (illustrative, not from a real run):

Suppose the parent component is "Authentication" and it has 3 files yielding clusters `[0, 1, 2, 3]`. The formatted prompt would include:

- `{component}` = `**Component:** Authentication - *Description*: Handles user login, token validation and session management.`
- `{cfg_clusters}` =
  ```
  ## Python - Component CFG
  Cluster 0: auth.login.LoginHandler.authenticate, auth.login.LoginHandler.validate_credentials
  Cluster 1: auth.token.TokenManager.generate, auth.token.TokenManager.refresh
  Cluster 2: auth.session.SessionStore.create, auth.session.SessionStore.invalidate
  Cluster 3: auth.middleware.AuthMiddleware.process_request
  ```

### step_final_analysis (details mode)

**Source**: `agents/prompts/claude_prompts.py:306-341` (constant `DETAILS_MESSAGE`).

**Template variables**: `{component}`, `{project_name}`, `{meta_context}`, `{project_type}`, `{cluster_analysis}`.

The `{cluster_analysis}` variable is filled with `cluster_analysis.llm_str()` from the previous step's output. At `agents/details_agent.py:151-155`, the agent appends an explicit group-name checklist to the prompt:

```python
prompt += (
    f"\n\n## All Group Names ({len(group_names)} total)\n"
    f"Every one of these names: {group_names} must appear in exactly one component's source_group_names\n"
)
```

```text
Create final sub-component architecture for the `{component}` subsystem of `{project_name}` optimized for flow representation.

Project Context:
{meta_context}

Cluster Analysis:
{cluster_analysis}

Instructions:
1. Review the named cluster groups above
2. Decide which named groups should be merged into final sub-components
3. For each sub-component, specify which named cluster groups it encompasses via source_group_names
4. Add key entities (2-5 most important classes/methods) for each sub-component
5. Define relationships between sub-components

Guidelines for {project_type} projects:
- Aim for 3-8 final sub-components
- Merge related cluster groups that serve a common purpose
- Each sub-component should have clear boundaries
- Include only architecturally significant relationships

Constraints:
- Focus on subsystem-specific functionality
- Exclude utility/logging sub-components
- Sub-components should translate well to flow diagram representation
```

**Synthetic example** (illustrative, not from a real run):

Following the previous example, `{cluster_analysis}` would be:

```
# Grouped Cluster Components
**Credential Verification** (cluster_ids: [0])
   Handles user credential validation during login flow.
**Token Lifecycle** (cluster_ids: [1])
   JWT generation and refresh token management.
**Session Management** (cluster_ids: [2, 3])
   Session creation, invalidation, and middleware request processing.
```

The appended checklist would be:

```
## All Group Names (3 total)
Every one of these names: ['Credential Verification', 'Token Lifecycle', 'Session Management'] must appear in exactly one component's source_group_names
```

---

## Tools

Tools are provided to the LLM agent via `CodeBoardingToolkit.get_agent_tools()` (`agents/tools/toolkit.py:91-101`). The agent receives 5 tools:

| Tool name | Class | Input schema | Source file |
|-----------|-------|-------------|-------------|
| `getSourceCode` | `CodeReferenceReader` | `code_reference: str` (qualified import path) | `agents/tools/read_source.py:25` |
| `readFile` | `ReadFileTool` | `file_path: str`, `line_number: int` | `agents/tools/read_file.py:19` |
| `getFileStructure` | `FileStructureTool` | `dir: str \| None` (default `"."`) | `agents/tools/read_file_structure.py:22` |
| `getClassHierarchy` | `CodeStructureTool` | `class_qualified_name: str` | `agents/tools/read_structure.py:14` |
| `getPackageDependencies` | `PackageRelationsTool` | `root_package: str` | `agents/tools/read_packages.py:26` |

Additional tools exist in the toolkit (`getControlFlowGraph`, `getMethodInvocationsTool`, `readDocs`, `readExternalDeps`) but are **not** passed to the React agent -- they are only available via `get_all_tools()`.

---

## Output Schema

### ClusterAnalysis (step_clusters_grouping output)

**Source**: `agents/agent_responses.py:123-135`

```python
class ClusterAnalysis(LLMBaseModel):
    cluster_components: list[ClustersComponent]
```

Where `ClustersComponent` (`agents/agent_responses.py:105-120`):

```python
class ClustersComponent(LLMBaseModel):
    name: str          # Short descriptive name for the group
    cluster_ids: list[int]  # CFG cluster IDs grouped together
    description: str   # Rationale, inter-group interactions, key qualified names
```

### AnalysisInsights (step_final_analysis output)

**Source**: `agents/agent_responses.py:243-268`

```python
class AnalysisInsights(LLMBaseModel):
    description: str                          # One-paragraph flow summary
    files: dict[str, FileEntry]               # Populated deterministically (excluded from LLM)
    components: list[Component]               # Sub-components
    components_relations: list[Relation]       # Inter-component relationships
```

Where `Component` (`agents/agent_responses.py:196-240`):

```python
class Component(LLMBaseModel):
    name: str
    description: str
    key_entities: list[SourceCodeReference]    # 2-5 critical classes/methods
    source_group_names: list[str]             # References to ClustersComponent.name
    source_cluster_ids: list[int]             # Populated deterministically (excluded from LLM)
    file_methods: list[FileMethodGroup]       # Populated deterministically (excluded from LLM)
    component_id: str                         # Populated deterministically (excluded from LLM)
```

And `Relation` (`agents/agent_responses.py:90-102`):

```python
class Relation(LLMBaseModel):
    relation: str       # Single phrase describing the relationship
    src_name: str       # Source component name
    dst_name: str       # Target component name
    src_id: str         # Populated deterministically (excluded from LLM)
    dst_id: str         # Populated deterministically (excluded from LLM)
    edge_count: int     # Populated deterministically (excluded from LLM)
    is_static: bool     # Populated deterministically (excluded from LLM)
```

And `SourceCodeReference` (`agents/agent_responses.py:48-87`):

```python
class SourceCodeReference(LLMBaseModel):
    qualified_name: str
    reference_file: str | None
    reference_start_line: int | None
    reference_end_line: int | None
```

---

## Validators

All validators are defined in `agents/validation.py`. Weights are declared at `agents/validation.py:21-27` in `VALIDATOR_WEIGHTS`. The default weight for unlisted validators is `5.0` (`DEFAULT_VALIDATOR_WEIGHT`, `agents/validation.py:28`).

### step_clusters_grouping validators

| Validator | Weight | Priority | Source |
|-----------|--------|----------|--------|
| `validate_cluster_coverage` | 20.0 | CRITICAL | `agents/validation.py:80` |

Checks that every expected cluster ID from the subgraph appears in exactly one `ClustersComponent.cluster_ids`. Also rejects empty `cluster_ids` lists and duplicate cluster assignments across groups. Up to 3 validation attempts (`max_validation_attempts=3`, `agents/details_agent.py:108`).

### step_final_analysis validators

| Validator | Weight | Priority | Source |
|-----------|--------|----------|--------|
| `validate_relation_component_names` | 5.0 | Secondary | `agents/validation.py:494` |
| `validate_group_name_coverage` | 20.0 | CRITICAL | `agents/validation.py:227` |
| `validate_key_entities` | 5.0 | Secondary | `agents/validation.py:322` |

- **`validate_group_name_coverage`** (CRITICAL): Bidirectional check -- every `ClustersComponent.name` must be referenced by at least one component's `source_group_names`, and every component must have at least one `source_group_name`. Includes auto-correction of case/whitespace/fuzzy mismatches before failing.
- **`validate_relation_component_names`** (Secondary): Ensures every `src_name` and `dst_name` in relations matches an existing component name.
- **`validate_key_entities`** (Secondary): Auto-corrects qualified names via loose matching against static analysis. Silently drops out-of-scope entities. Only fails if a component ends up with zero key entities.

Up to 3 validation attempts (`max_validation_attempts=3`, `agents/details_agent.py:169`).

The scoring system in `_score_result` (`agents/agent.py:225-250`) uses weighted sum. Feedback for validators with weight >= 10.0 is tagged `[CRITICAL]`; below that threshold, `[Secondary]`. The validation loop (`_validation_invoke`, `agents/agent.py:252-336`) tracks the best-scoring result across all attempts and returns it.

---

## Deterministic Pre-processing

All pre-processing runs in `run()` step 1 (`agents/details_agent.py:210`) before the first LLM call. Prompt assembly for each step is described in the User Prompt section above.

### 1. Subgraph construction from component files

**Source**: `agents/cluster_methods_mixin.py:226-305` (`_create_strict_component_subgraph`)

- Extracts `component.file_methods` file paths (`agents/cluster_methods_mixin.py:245`).
- Converts to absolute paths and builds a set for comparison (`agents/cluster_methods_mixin.py:251-254`).
- For each language, calls `cfg.filter_by_files(assigned_file_set)` to produce a `sub_cfg` containing only nodes from the component's files (`agents/cluster_methods_mixin.py:263`).
- Clusters the subgraph via `sub_cfg.cluster()` (`agents/cluster_methods_mixin.py:269`).
- If the subgraph exceeds `MAX_LLM_CLUSTERS`, merges into super-clusters via `merge_clusters()` (`agents/cluster_methods_mixin.py:272-278`).

### 2. Method-level expansion (conditional)

**Source**: `agents/cluster_methods_mixin.py:170-224` (`_expand_to_method_level_clusters`)

- If the subgraph has fewer than `MIN_CLUSTERS_THRESHOLD` (= 5, `constants.py:14`) clusters, expands to method-level granularity.
- Each callable node (function/method) gets its own synthetic cluster ID (`agents/cluster_methods_mixin.py:198-205`).
- If still below threshold after callables, class-type nodes are also included (`agents/cluster_methods_mixin.py:209-215`).
- Returns a new `ClusterResult` with `strategy="method_level_expansion"` (`agents/cluster_methods_mixin.py:219-224`).

### 3. Cross-language budget enforcement

**Source**: `agents/cluster_methods_mixin.py:285-287`

- If multiple languages are present in `cluster_results`, calls `enforce_cross_language_budget()` to ensure unique cluster IDs and a combined budget.

### 4. Cluster string formatting

**Source**: `agents/cluster_methods_mixin.py:289-299`

- Builds the formatted cluster string from `subgraph_cfgs[lang].to_cluster_string()` for each language, prepended with `## {lang} - Component CFG` headers.

---

## Deterministic Post-processing

These steps happen after both LLM calls return, in `run()` (`agents/details_agent.py:219-239`).

### 1. Hierarchical component ID assignment

**Source**: `agents/agent_responses.py:270-297` (`assign_component_ids`), called at `agents/details_agent.py:219`.

- Called with `parent_id=component.component_id` (e.g., `"1"`).
- Each sub-component receives an ID like `"1.1"`, `"1.2"`, etc. (`agents/agent_responses.py:281-282`).
- Relation `src_id`/`dst_id` fields are resolved from a `name_to_id` lookup (`agents/agent_responses.py:285-296`).

### 2. Cluster ID resolution from group names

**Source**: `agents/cluster_methods_mixin.py:153-168` (`_resolve_cluster_ids_from_groups`), called at `agents/details_agent.py:222`.

- Builds a case-insensitive `group_name_to_ids` map from `cluster_analysis.cluster_components` (`agents/cluster_methods_mixin.py:155-157`).
- For each component, resolves `source_group_names` to `source_cluster_ids` via this map (`agents/cluster_methods_mixin.py:160-168`).
- Logs warnings for unresolved group names.

### 3. File methods population

**Source**: `agents/cluster_methods_mixin.py:573-613` (`populate_file_methods`), called at `agents/details_agent.py:228`.

- Passes `subgraph_cluster_results` and `subgraph_cfgs` to scope node collection to the component's filtered graph.
- Builds `cluster_to_component` and `node_to_cluster` maps (`agents/cluster_methods_mixin.py:600-601`).
- Assigns every node to a component via: (a) its cluster mapping, (b) file co-location, (c) graph distance (nearest cluster via undirected shortest path), or (d) fallback to first component (`agents/cluster_methods_mixin.py:480-545`).
- Groups assigned nodes into `FileMethodGroup` lists sorted by file and line (`agents/cluster_methods_mixin.py:385-431`).
- Builds a top-level `files` index on the analysis (`agents/cluster_methods_mixin.py:611`).

### 4. Static inter-component relations from CFG edges

**Source**: `agents/cluster_methods_mixin.py:615-633` (`build_static_relations`), called at `agents/details_agent.py:231`.

- Uses `subgraph_cfgs` to build a `node_to_component` map and extract `static_relations` from CFG edges (`agents/cluster_methods_mixin.py:631-632`).
- Merges with LLM-produced relations: LLM+static match keeps LLM label with edge count; LLM-only relations are dropped; static-only relations are added with label "calls" (`agents/cluster_methods_mixin.py:633`).

### 5. Source code reference resolution

**Source**: `static_analyzer/reference_resolve_mixin.py:19-39` (`fix_source_code_reference_lines`), called at `agents/details_agent.py:234`.

- For each `key_entity` in each component, attempts exact match, loose match, and file-path resolution against static analysis data.
- Removes unresolved references and converts all paths to relative (`static_analyzer/reference_resolve_mixin.py:164-187`).

### 6. Key entity deduplication

**Source**: `agents/cluster_methods_mixin.py:104-151` (`_ensure_unique_key_entities`), called at `agents/details_agent.py:237`.

- Iterates all components and tracks `qualified_name` -> first-seen component.
- If a duplicate is found, keeps the entity in the component whose `file_methods` actually contain the reference file (`agents/cluster_methods_mixin.py:131-135`). Otherwise keeps it in the first-seen component.
- Removes the duplicate from the other component.
