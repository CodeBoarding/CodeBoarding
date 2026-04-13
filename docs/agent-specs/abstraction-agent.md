# AbstractionAgent -- Technical Spec Sheet

| Field | Value |
|-------|-------|
| **Class** | `AbstractionAgent` |
| **File** | `agents/abstraction_agent.py` |
| **Inheritance** | `ClusterMethodsMixin`, `CodeBoardingAgent` (which itself inherits `ReferenceResolverMixin`, `MonitoringMixin`) |
| **LLM Steps** | 2 sequential steps executed by `run()`: (1) `step_clusters_grouping` -- groups CFG cluster IDs into logical components; (2) `step_final_analysis` -- produces final architectural components with entities and relations |

---

## System Prompt

Both LLM steps share the same system message. It is set once in `__init__` (`agents/abstraction_agent.py:48`) via `get_system_message()` and stored as a `SystemMessage` on the base class (`agents/agent.py:58`). The raw template is returned by the prompt factory; for the default provider the literal text lives at `agents/prompts/claude_prompts.py:19-41`.

**Note:** The template contains `{project_name}`, `{meta_context}`, and `{project_type}` placeholders, but AbstractionAgent passes the template to `SystemMessage(content=...)` without calling `.format()`. The placeholders remain as literal curly-brace tokens in the system message sent to the LLM.

```text
You are a software architecture expert analyzing {project_name} with comprehensive diagram generation optimization.

<context>
Project context: {meta_context}

The goal is to generate documentation that a new engineer can understand within their first week, along with interactive visual diagrams that help navigate the codebase.
</context>

<instructions>
1. Analyze the provided CFG data first - identify patterns and structures suitable for flow graph representation
2. Use tools when information is missing to ensure accuracy
3. Focus on architectural patterns for {project_type} projects with clear component boundaries
4. Consider diagram generation needs - components should have distinct visual boundaries
5. Create analysis suitable for both documentation and visual diagram generation
</instructions>

<thinking>
Focus on:
- Components with distinct visual boundaries for flow graph representation
- Source file references for interactive diagram elements
- Clear data flow optimization excluding utility/logging components that clutter diagrams
- Architectural patterns that help new developers understand the system quickly
</thinking>
```

---

## User Prompt (templates)

### step_clusters_grouping

**Template source:** `agents/prompts/claude_prompts.py:43-80` (constant `CLUSTER_GROUPING_MESSAGE`)
**PromptTemplate registered at:** `agents/abstraction_agent.py:54-57`
**Input variables:** `project_name`, `cfg_clusters`, `meta_context`, `project_type`

```text
Analyze and GROUP the Control Flow Graph clusters for `{project_name}`.

Project Context:
{meta_context}

Project Type: {project_type}

The CFG has been pre-clustered into groups of related methods/functions. Each cluster represents methods that call each other frequently.

CFG Clusters:
{cfg_clusters}

Your Task:
GROUP similar clusters together into logical components based on their relationships and purpose.

Instructions:
1. Analyze the clusters shown above and identify which ones work together or are functionally related
2. Group related clusters into meaningful components
3. A component can contain one or more cluster IDs (e.g., [1], [2, 5], or [3, 7, 9])
4. For each grouped component, provide:
   - **name**: Short, descriptive name for this group
   - **cluster_ids**: List of cluster IDs that belong together
   - **description**: Comprehensive explanation including:
     * What this component does
     * What is its main flow/purpose
     * WHY these specific clusters are grouped together
     * How this group interacts with other cluster groups
     * The most important classes/methods in this group

Focus on:
- Creating cohesive, logical groupings that reflect the actual {project_type} architecture
- Semantic meaning based on method names, call patterns, and architectural context
- Clear justification for why clusters belong together
- Describing inter-group interactions based on the inter-cluster connections

Output Format:
Return a ClusterAnalysis with cluster_components using ClustersComponent model.
```

**Synthetic example** (illustrative, not from a real run):

Given a Python web project "myapp" with 6 clusters, the formatted prompt would replace:
- `{project_name}` = `myapp`
- `{meta_context}` = `Project Type: web framework\nDomain: web development\n...`
- `{project_type}` = `web framework`
- `{cfg_clusters}` = a multi-line string produced by `_build_cluster_string`, e.g.:

```text
## Python - Clusters

Cluster 0 (3 nodes):
  myapp.auth.login -> myapp.auth.verify_token
  myapp.auth.verify_token -> myapp.auth.decode_jwt

Cluster 1 (2 nodes):
  myapp.db.connect -> myapp.db.execute_query

## All Cluster IDs (2 total)
Every one of these IDs: [0, 1] must appear in exactly one group.
```

Additionally, `step_clusters_grouping` calls `_validation_invoke` with `validate_cluster_coverage` and `max_validation_attempts=3` (`agents/abstraction_agent.py:83-92`).

---

### step_final_analysis

**Template source:** `agents/prompts/claude_prompts.py:82-115` (constant `FINAL_ANALYSIS_MESSAGE`)
**PromptTemplate registered at:** `agents/abstraction_agent.py:58-61`
**Input variables:** `project_name`, `cluster_analysis`, `meta_context`, `project_type`

```text
Create final component architecture for `{project_name}` optimized for flow representation.

Project Context:
{meta_context}

Cluster Analysis:
{cluster_analysis}

Instructions:
1. Review the named cluster groups above
2. Decide which named groups should be merged into final components
3. For each component, specify which named cluster groups it encompasses via source_group_names
4. Add key entities (2-5 most important classes/methods) for each component using SourceCodeReference
5. Define relationships between components

Guidelines for {project_type} projects:
- Aim for 5-8 final components
- Merge related cluster groups that serve a common purpose
- Each component should have clear boundaries
- Include only architecturally significant relationships

Required outputs:
- Description: One paragraph explaining the main flow and purpose
- Components: Each with name, description, source_group_names, key_entities
- Relations: Max 2 relationships per component pair

Constraints:
- Focus on highest level architectural components
- Exclude utility/logging components
- Components should translate well to flow diagram representation
```

**Dynamic suffix** -- after formatting the template, the code appends a group-name checklist (`agents/abstraction_agent.py:115-119`):

```text
## All Group Names (N total)
Every one of these names must appear in exactly one component's source_group_names: ['Authentication', 'Data Pipeline', ...]
```

**Synthetic example:**

- `{project_name}` = `myapp`
- `{cluster_analysis}` = the `llm_str()` of the `ClusterAnalysis` from step 1, e.g.:
  ```
  # Grouped Cluster Components
  **Authentication** (cluster_ids: [0])
     Handles login, token verification, JWT decoding...
  **Database** (cluster_ids: [1])
     Manages DB connections and query execution...
  ```
- `{meta_context}` and `{project_type}` = same as step 1

This step calls `_validation_invoke` with three validators (`validate_relation_component_names`, `validate_group_name_coverage`, `validate_key_entities`) and `max_validation_attempts=3` (`agents/abstraction_agent.py:129-139`).

---

## Tools

AbstractionAgent inherits the full `CodeBoardingToolkit` via `CodeBoardingAgent.__init__` (`agents/agent.py:50-51`). The agent is constructed with `create_agent(model, tools=self.toolkit.get_agent_tools())` (`agents/agent.py:53-56`), which provides **5 tools** to the ReAct agent loop. The remaining tools are available programmatically but not passed to the LLM agent.

### Tools exposed to the LLM agent (via `get_agent_tools`)

| # | Tool name | Class | Input schema | File |
|---|-----------|-------|--------------|------|
| 1 | `getSourceCode` | `CodeReferenceReader` | `code_reference: str` | `agents/tools/read_source.py:25` |
| 2 | `readFile` | `ReadFileTool` | `file_path: str, line_number: int` | `agents/tools/read_file.py:19` |
| 3 | `getFileStructure` | `FileStructureTool` | `dir: str \| None = "."` | `agents/tools/read_file_structure.py:22` |
| 4 | `getClassHierarchy` | `CodeStructureTool` | `class_qualified_name: str` | `agents/tools/read_structure.py:14` |
| 5 | `getPackageDependencies` | `PackageRelationsTool` | `root_package: str` | `agents/tools/read_packages.py:26` |

Reference: `agents/tools/toolkit.py:91-101`

### Additional tools available programmatically (via `get_all_tools`)

| # | Tool name | Class | File |
|---|-----------|-------|------|
| 6 | `getControlFlowGraph` | `GetCFGTool` | `agents/tools/read_cfg.py:8` |
| 7 | `getMethodInvocationsTool` | `MethodInvocationsTool` | `agents/tools/get_method_invocations.py:14` |
| 8 | `readDocs` | `ReadDocsTool` | `agents/tools/read_docs.py:22` |
| 9 | `readExternalDeps` | `ExternalDepsTool` | `agents/tools/get_external_deps.py:15` |
| 10 | `readDiffFile` | `ReadDiffTool` (created on-demand) | `agents/tools/read_git_diff.py:22` |

Reference: `agents/tools/toolkit.py:103-119`

---

## Output Schema

### Step 1: `ClusterAnalysis` (step_clusters_grouping)

**Source:** `agents/agent_responses.py:123-135`

```json
{
  "type": "object",
  "required": ["cluster_components"],
  "properties": {
    "cluster_components": {
      "type": "array",
      "description": "Grouped clusters into logical components.",
      "items": {
        "$ref": "#/$defs/ClustersComponent"
      }
    }
  },
  "$defs": {
    "ClustersComponent": {
      "type": "object",
      "required": ["name", "cluster_ids", "description"],
      "properties": {
        "name": {
          "type": "string",
          "description": "Short, descriptive name for this cluster group"
        },
        "cluster_ids": {
          "type": "array",
          "items": { "type": "integer" },
          "description": "List of cluster IDs from the CFG analysis that are grouped together"
        },
        "description": {
          "type": "string",
          "description": "Explanation of what this component does, its main flow, WHY these clusters are grouped together, how it interacts with other cluster groups, and the most important classes/methods"
        }
      }
    }
  }
}
```

Reference: `ClustersComponent` at `agents/agent_responses.py:105-120`, `ClusterAnalysis` at `agents/agent_responses.py:123-135`.

---

### Step 2: `AnalysisInsights` (step_final_analysis)

**Source:** `agents/agent_responses.py:243-268`

```json
{
  "type": "object",
  "required": ["description", "components", "components_relations"],
  "properties": {
    "description": {
      "type": "string",
      "description": "One paragraph explaining the functionality represented by this graph."
    },
    "files": {
      "type": "object",
      "description": "Top-level file index (populated post-LLM, excluded from LLM output).",
      "additionalProperties": { "$ref": "#/$defs/FileEntry" }
    },
    "components": {
      "type": "array",
      "items": { "$ref": "#/$defs/Component" }
    },
    "components_relations": {
      "type": "array",
      "items": { "$ref": "#/$defs/Relation" }
    }
  },
  "$defs": {
    "Component": {
      "type": "object",
      "required": ["name", "description", "key_entities"],
      "properties": {
        "name": { "type": "string" },
        "description": { "type": "string" },
        "key_entities": {
          "type": "array",
          "items": { "$ref": "#/$defs/SourceCodeReference" },
          "description": "2-5 most important classes/methods"
        },
        "source_group_names": {
          "type": "array",
          "items": { "type": "string" },
          "description": "Names of cluster groups this component encompasses"
        },
        "source_cluster_ids": {
          "type": "array",
          "items": { "type": "integer" },
          "description": "(excluded from LLM; populated deterministically)"
        },
        "file_methods": {
          "type": "array",
          "items": { "$ref": "#/$defs/FileMethodGroup" },
          "description": "(excluded from LLM; populated deterministically)"
        },
        "component_id": {
          "type": "string",
          "description": "(excluded from LLM; assigned deterministically)"
        }
      }
    },
    "Relation": {
      "type": "object",
      "required": ["relation", "src_name", "dst_name"],
      "properties": {
        "relation": { "type": "string", "description": "Single phrase for the relationship" },
        "src_name": { "type": "string" },
        "dst_name": { "type": "string" },
        "src_id": { "type": "string", "description": "(excluded from LLM)" },
        "dst_id": { "type": "string", "description": "(excluded from LLM)" },
        "edge_count": { "type": "integer", "description": "(excluded from LLM)" },
        "is_static": { "type": "boolean", "description": "(excluded from LLM)" }
      }
    },
    "SourceCodeReference": {
      "type": "object",
      "required": ["qualified_name"],
      "properties": {
        "qualified_name": { "type": "string" },
        "reference_file": { "type": "string", "nullable": true },
        "reference_start_line": { "type": "integer", "nullable": true },
        "reference_end_line": { "type": "integer", "nullable": true }
      }
    }
  }
}
```

References: `AnalysisInsights` at `agents/agent_responses.py:243-268`, `Component` at `agents/agent_responses.py:196-240`, `Relation` at `agents/agent_responses.py:90-102`, `SourceCodeReference` at `agents/agent_responses.py:48-87`.

---

## Validators

All validators are defined in `agents/validation.py`. Weights are declared in the `VALIDATOR_WEIGHTS` dict (`agents/validation.py:21-27`). The default weight for unlisted validators is `5.0` (`agents/validation.py:28`).

Validators with weight >= 10 are tagged `[CRITICAL]` in feedback; others are tagged `[Secondary]` (`agents/agent.py:288-289`, `agents/agent.py:320-321`).

### Step 1 validators (`step_clusters_grouping`)

| Validator | Weight | Level | Reference |
|-----------|--------|-------|-----------|
| `validate_cluster_coverage` | 20.0 | CRITICAL | `agents/validation.py:80-144` |

Checks that: (a) every expected cluster ID from `get_all_cluster_ids(cluster_results)` is present in the output, (b) no `ClustersComponent` has an empty `cluster_ids` list, (c) no cluster ID appears in multiple groups.

### Step 2 validators (`step_final_analysis`)

| Validator | Weight | Level | Reference |
|-----------|--------|-------|-----------|
| `validate_relation_component_names` | 5.0 | Secondary | `agents/validation.py:494-535` |
| `validate_group_name_coverage` | 20.0 | CRITICAL | `agents/validation.py:227-319` |
| `validate_key_entities` | 5.0 | Secondary | `agents/validation.py:322-428` |

- **`validate_group_name_coverage`** -- Validates bidirectional coverage: every cluster group name from step 1 must be referenced by at least one component's `source_group_names`, and every component must have at least one `source_group_name`. Includes auto-correction of case/whitespace/fuzzy mismatches before failing.
- **`validate_relation_component_names`** -- Ensures every `src_name` and `dst_name` in `components_relations` references an existing component name.
- **`validate_key_entities`** -- Auto-corrects qualified names via loose matching against static analysis. Silently drops out-of-scope entities. Only fails if a component has zero valid key entities after cleanup.

### Scoring and feedback loop

`_validation_invoke` (`agents/agent.py:252-336`) runs all validators, computes a weighted score via `score_validation_results` (`agents/validation.py:57-77`), and retries up to `max_validation_attempts` (set to 3 for both steps). On imperfect scores, it sends feedback using `VALIDATION_FEEDBACK_MESSAGE` (`agents/prompts/claude_prompts.py:240-252`) with `[CRITICAL]`/`[Secondary]` tags sorted by weight descending. The best-scoring result across all attempts is returned.

---

## Deterministic Pre-processing

### Before step 1 (`step_clusters_grouping`)

1. **Build all cluster results** -- `run()` calls `build_all_cluster_results(self.static_analysis)` (`agents/abstraction_agent.py:143`) to pre-compute `dict[str, ClusterResult]` for all languages. This delegates to `static_analyzer/cluster_helpers.py:build_all_cluster_results`.

2. **Build cluster string** -- `step_clusters_grouping` calls `self._build_cluster_string(programming_langs, cluster_results)` (`agents/abstraction_agent.py:74`, method at `agents/cluster_methods_mixin.py:59-102`). This iterates over all languages, calls `cfg.to_cluster_string()` for each, and appends a checklist of all cluster IDs so the LLM knows the full set.

3. **Extract meta context** -- `self.meta_context.llm_str()` serializes the `MetaAnalysisInsights` (produced by a prior MetaAnalysisAgent run) into a human-readable string (`agents/abstraction_agent.py:68-69`).

4. **Format prompt** -- The `PromptTemplate` is formatted with `project_name`, `cfg_clusters`, `meta_context`, `project_type` (`agents/abstraction_agent.py:76-81`).

5. **Build validation context** -- `ValidationContext` is constructed with `cluster_results` and `expected_cluster_ids` from `get_all_cluster_ids(cluster_results)` (`agents/abstraction_agent.py:87-90`).

### Before step 2 (`step_final_analysis`)

1. **Serialize step-1 output** -- `llm_cluster_analysis.llm_str()` converts the `ClusterAnalysis` into a Markdown string for the prompt (`agents/abstraction_agent.py:104`).

2. **Extract group names** -- A list of all group names from step 1 is built (`agents/abstraction_agent.py:106`).

3. **Format prompt + append group-name checklist** -- The template is formatted, then a suffix listing all group names is appended to enforce coverage (`agents/abstraction_agent.py:108-119`).

4. **Build validation context** -- `ValidationContext` is constructed with `cluster_results`, `cfg_graphs` (per-language), `static_analysis`, and `llm_cluster_analysis` (`agents/abstraction_agent.py:122-127`).

---

## Deterministic Post-processing

After both LLM steps complete, `run()` executes the following deterministic pipeline (`agents/abstraction_agent.py:141-165`):

### Step 3: Assign hierarchical component IDs

`assign_component_ids(analysis)` (`agents/abstraction_agent.py:151`, function at `agents/agent_responses.py:270-296`)

Assigns sequential string IDs ("1", "2", "3", ...) to each component. Also resolves `src_id`/`dst_id` on relations by looking up component names.

### Step 4: Resolve cluster IDs from group names

`self._resolve_cluster_ids_from_groups(analysis, cluster_analysis)` (`agents/abstraction_agent.py:153`, method at `agents/cluster_methods_mixin.py:153-168`)

Performs case-insensitive lookup of each component's `source_group_names` against the step-1 `ClusterAnalysis` to populate `source_cluster_ids`. Logs warnings for unresolved group names.

### Step 5: Populate file_methods

`self.populate_file_methods(analysis, cluster_results)` (`agents/abstraction_agent.py:155`, method at `agents/cluster_methods_mixin.py:573-613`)

Node-centric assignment guaranteeing 100% coverage:
1. Builds `cluster_id -> Component` map from `source_cluster_ids`.
2. Builds `node_name -> cluster_id` map from cluster results.
3. Assigns every CFG node to a component via its cluster. Orphan nodes are assigned by file co-location, then graph distance (shortest path), then fallback to first component.
4. Groups assigned nodes into `FileMethodGroup` per file, filtering to callable/class types only.
5. Builds the top-level `files` index on the analysis.

### Step 6: Build static inter-component relations

`self.build_static_relations(analysis)` (`agents/abstraction_agent.py:158`, method at `agents/cluster_methods_mixin.py:615-633`)

Replaces LLM-only relations with statically-backed ones using CFG edges:
- LLM + static match: keeps LLM label, attaches `edge_count`.
- LLM only (no static backing): dropped.
- Static only: added with auto-label "calls".

Delegates to `static_analyzer/cluster_relations.py:build_component_relations` and `merge_relations`.

### Step 7: Fix source code reference lines

`analysis = self.fix_source_code_reference_lines(analysis)` (`agents/abstraction_agent.py:161`, method at `static_analyzer/reference_resolve_mixin.py:19-39`)

For each `key_entity` `SourceCodeReference` in every component:
- If file + line numbers are already resolved and valid, skip.
- Otherwise, try exact match, then loose match against static analysis.
- Converts all paths to relative.
- Removes completely unresolved references.

### Step 8: Ensure unique key entities

`self._ensure_unique_key_entities(analysis)` (`agents/abstraction_agent.py:163`, method at `agents/cluster_methods_mixin.py:104-151`)

Deduplicates `key_entities` across components by `qualified_name`. When a duplicate is found, it is kept in whichever component owns the entity's file in its `file_methods`. Otherwise, first-seen wins.
