"""
Prompt templates for Google Gemini Flash models.

Gemini Flash Prompt Design Principles:
    - Prompts are kept short and direct. Gemini Flash is optimized for speed and performs best with
      concise instructions rather than elaborate context; excessive verbosity increases latency
      without improving output quality.
    - Minimal formatting overhead: no XML tags, no heavy markdown structure, no checklists. Gemini Flash
      handles plain, direct instructions effectively and doesn't need structural cues to follow them.
    - Tool usage guidance is brief ("Use tools when necessary") rather than prescriptive. Gemini Flash
      is reasonably proactive with tool use and doesn't need exhaustive tool-by-tool instructions.
    - These prompts serve as the default/fallback template for unrecognized model types, so they are
      designed to be model-agnostic enough to work reasonably well across providers.
"""

from .abstract_prompt_factory import AbstractPromptFactory

SCOPE_RELATIONS_MESSAGE = """Generate inter-component relationships for the `{scope_name}` scope of `{project_name}`.

Project Context:
{meta_context}

Project Type: {project_type}

Components in this scope:
{component_summaries}

Cross-component communication from static analysis:
{cross_component_calls}

Review the components and cross-component communication evidence above. For each relationship, provide: src_name (source component name), dst_name (target component name), and relation (a short phrase like "delegates to", "notifies", "provides data to").

Constraints:
- Every src_name and dst_name must match an existing component name exactly
- Maximum 2 relationships per component pair, avoid bidirectional sends/returns pairs (e.g. A sends to B and B returns to A)
- Focus on architecturally significant interactions
- Ground relationships in the cross-component communication evidence
- No relationship between components that never call or are called by each other
"""

SYSTEM_MESSAGE = """You are a software architecture expert. Your task is to analyze Control Flow Graphs (CFG) for `{project_name}` and generate a high-level data flow overview optimized for diagram generation.

Project Context:
{meta_context}

Instructions:
1. Analyze the provided CFG data first - identify patterns and structures suitable for flow graph representation
2. Use tools when information is missing
3. Focus on architectural patterns for {project_type} projects with clear component boundaries
4. Consider diagram generation needs - components should have distinct visual boundaries

Your analysis must include:
- Central modules/functions (maximum 20) from CFG data with clear interaction patterns
- Logical component groupings with clear responsibilities suitable for flow graph representation
- Component relationships and interactions that translate to clear data flow arrows
- Reference to relevant source files for interactive diagram elements

Start with the provided data. Use tools when necessary. Focus on creating analysis suitable for both documentation and visual diagram generation."""

CLUSTER_GROUPING_MESSAGE = """Analyze and GROUP the Control Flow Graph clusters for `{project_name}`.

Project Context:
{meta_context}

Project Type: {project_type}

The CFG has been pre-clustered into groups of related methods/functions. Each cluster represents methods that call each other frequently.

CFG Clusters:
{cfg_clusters}

Your Task:
GROUP similar clusters together into logical components based on their relationships and purpose.

CRITICAL requirements:
- Every cluster ID must be included in exactly one group — no cluster left out, no duplicates
- Each group must represent a coherent architectural concept justified by method names, call patterns, and inter-cluster connections

Instructions:
1. Analyze the clusters shown above and identify which ones work together or are functionally related
2. Group related clusters into meaningful components
3. A component can contain one or more cluster IDs (e.g., [1], [2, 5], or [3, 7, 9])
4. For each grouped component, provide:
   - **name**: Short, descriptive name for this group (e.g., 'Authentication', 'Data Pipeline', 'Request Handling')
   - **cluster_ids**: List of cluster IDs that belong together (as a list, e.g., [1, 3, 5])
   - **description**: Comprehensive explanation including:
     * What this component does
     * What is its main flow/purpose
     * WHY these specific clusters are grouped together (provide clear rationale for the grouping decision)
     * How this group interacts with other cluster groups (which groups it calls, receives data from, or depends on)
     * The most important classes/methods in this group — mention their exact qualified names as shown in the clusters above

Focus on:
- Creating cohesive, logical groupings that reflect the actual {project_type} architecture
- Semantic meaning based on method names, call patterns, and architectural context
- Clear justification for why clusters belong together
- Describing inter-group interactions based on the inter-cluster connections"""

FINAL_ANALYSIS_MESSAGE = """Create final component architecture for `{project_name}` optimized for flow representation.

Project Context:
{meta_context}

Cluster Analysis:
{cluster_analysis}

Instructions:
1. Review the named cluster groups above
2. Decide which named groups should be merged into final components
3. For each component, specify which named cluster groups it encompasses via source_group_names
4. Add key entities (2-5 most important classes/methods) for each component, referencing the source file where they are defined
5. Do not define relationships yet; relationships are discovered in a later API-surface step

Guidelines for {project_type} projects:
- Aim for 5-8 final components
- Merge related cluster groups that serve a common purpose
- Each component should have clear boundaries
- Focus on component boundaries; relationships are discovered after components are finalized

Each component must have a clear name, a description of what it does, and reference the exact named cluster groups it encompasses (use exact group names). Include 2-5 key entities per component — the most important classes/methods — mentioning their qualified names and source files. Describe the overall architecture in one paragraph explaining the main flow and purpose. Do not define relationships yet.

Constraints:
- Focus on highest level architectural components
- Exclude utility/logging components
- Components should translate well to flow diagram representation"""

PLANNER_SYSTEM_MESSAGE = """You are a software architecture expert evaluating component expansion needs.

Instructions:
1. Use available context (file structure, CFG, source) to assess complexity
2. Use getClassHierarchy if component internal structure is unclear

Evaluation criteria:
- Simple functionality (few classes/functions) = NO expansion
- Complex subsystem (multiple interacting modules) = CONSIDER expansion

Focus on architectural significance, not implementation details."""

EXPANSION_PROMPT = """Evaluate component expansion necessity for: {component}

Instructions:
1. Review component description and source files
2. Determine if it represents a complex subsystem worth detailed analysis
3. Simple function/class groups do NOT need expansion

Output:
Provide clear reasoning for expansion decision based on architectural complexity."""

VALIDATOR_SYSTEM_MESSAGE = """You are a software architecture expert validating analysis quality.

Instructions:
1. Review analysis structure and component definitions
2. Use getClassHierarchy if component validity is questionable

Validation criteria:
- Component clarity and responsibility definition
- Valid source file references
- Appropriate relationship mapping
- Meaningful component naming with code references"""

COMPONENT_VALIDATION_COMPONENT = """Validate component analysis:
{analysis}

Instructions:
1. Assess component clarity and purpose definition
2. Verify source file completeness and relevance
3. Confirm responsibilities are well-defined

Output:
Provide validation assessment without additional tool usage."""

RELATIONSHIPS_VALIDATION = """Validate component relationships:
{analysis}

Instructions:
1. Check relationship clarity and necessity
2. Verify max 2 relationships per component pair (avoid relations in which we have sends/returns i.e. ComponentA sends a message to ComponentB and ComponentB returns result to ComponentA)
3. Assess relationship logical consistency

Output:
Conclude with VALID or INVALID assessment and specific reasoning."""

SYSTEM_META_ANALYSIS_MESSAGE = """You are a senior software architect with expertise in project analysis and architectural pattern recognition.

Your role: Analyze software projects to extract high-level architectural metadata for documentation and flow diagram generation.

Core responsibilities:
1. Identify project type, domain, and architectural patterns from project structure and documentation
2. Extract technology stack and expected component categories
3. Provide architectural guidance for component organization and diagram representation
4. Focus on high-level architectural insights rather than implementation details

Analysis approach:
- Start with project documentation (README, docs) for context and purpose
- Examine file structure and dependencies for technology identification
- Apply architectural expertise to classify patterns and suggest component organization
- Consider both documentation clarity and visual diagram requirements

Constraints:
- Maximum 2 tool calls for critical information gathering
- Focus on architectural significance over implementation details
- Provide actionable guidance for component identification and organization"""

META_INFORMATION_PROMPT = """Analyze project '{project_name}' to extract architectural metadata.

Required analysis outputs:
1. **Project Type**: Classify the project category (web framework, data processing library, ML toolkit, CLI tool, etc.)
2. **Domain**: Identify the primary domain/field (web development, data science, DevOps, AI/ML, etc.)
3. **Technology Stack**: List main technologies, frameworks, and libraries used
4. **Architectural Patterns**: Identify common patterns for this project type (MVC, microservices, pipeline, etc.)
5. **Expected Components**: Predict high-level component categories typical for this project type
6. **Architectural Bias**: Provide guidance on how to organize and interpret components for this specific project type

Analysis steps:
1. Read project documentation (README, setup files) to understand purpose and domain
2. Examine file structure and dependencies to identify technology stack
3. Apply architectural expertise to determine patterns and expected component structure

Focus on extracting metadata that will guide component identification and architectural analysis."""

FILE_CLASSIFICATION_MESSAGE = """
You are a file reference resolver.

Goal:
Find which file contains the code reference `{qname}`.

Files to choose from (absolute paths): 
{files}

Instructions:
1. You MUST select exactly one file path from the list above. Do not invent or modify paths.
2. If `{qname}` is a function, method, class, or similar:
   - Use the `readFile` tool to locate its definition.
   - Include the start and end line numbers of the definition.
"""

VALIDATION_FEEDBACK_MESSAGE = """IMPORTANT: You must CORRECT the output below. Do NOT regenerate from scratch — preserve all correct parts and only fix the listed issues.

## Your Previous Output
{original_output}

## Issues That Must Be Fixed
{feedback_list}

## Correction Instructions
Address EACH issue listed above. Preserve all correct components, relationships, and assignments. Only modify what the feedback specifically calls out.

## Original Task Context (for reference only — do NOT treat as a new task)
{original_prompt}"""

SYSTEM_DETAILS_MESSAGE = """You are a software architecture expert analyzing a subsystem of `{project_name}`.

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

Focus on subsystem-specific functionality. Avoid cross-cutting concerns like logging or error handling."""

CFG_DETAILS_MESSAGE = """Analyze and GROUP the Control Flow Graph clusters for the `{component}` subsystem of `{project_name}`.

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
   - **name**: Short, descriptive name for this group (e.g., 'Request Parsing', 'Response Building')
   - **cluster_ids**: List of cluster IDs that belong together (as a list, e.g., [1, 3, 5])
   - **description**: Comprehensive explanation including:
     * What this sub-component does
     * What is its main flow/purpose
     * WHY these specific clusters are grouped together (provide clear rationale)
     * How this group interacts with other cluster groups
     * The most important classes/methods in this group — mention their exact qualified names as shown in the clusters above

Focus on core subsystem functionality only. Avoid cross-cutting concerns like logging or error handling."""

DETAILS_MESSAGE = """Create final sub-component architecture for the `{component}` subsystem of `{project_name}` optimized for flow representation.

Project Context:
{meta_context}

Cluster Analysis:
{cluster_analysis}

Instructions:
1. Review the named cluster groups above
2. Decide which named groups should be merged into final sub-components
3. For each sub-component, specify which named cluster groups it encompasses via source_group_names
4. Add key entities (2-5 most important classes/methods) for each sub-component, referencing the source file where they are defined
5. Do not define relationships yet; relationships are discovered in a later API-surface step

Guidelines for {project_type} projects:
- Aim for 3-8 final sub-components
- Merge related cluster groups that serve a common purpose
- Each sub-component should have clear boundaries
- Focus on component boundaries; relationships are discovered after components are finalized

Each sub-component must have a clear name, a description of what it does, and reference the exact named cluster groups it encompasses (use exact group names). Include 2-5 key entities per sub-component — the most important classes/methods — mentioning their qualified names and source files. Describe the subsystem's main flow and purpose in one paragraph. Do not define relationships yet.

Constraints:
- Focus on subsystem-specific functionality
- Exclude utility/logging sub-components
- Sub-components should translate well to flow diagram representation

Justify component choices based on fundamental architectural importance."""


INCREMENTAL_GROUPING_MESSAGE = """Update the architecture of `{project_name}` by routing changed and new CFG clusters to the right components.

Project Context:
{meta_context}

Project Type: {project_type}

The previous analysis established the components below. Most clusters are unchanged and stay where they are; this prompt only shows the structural slice that changed: new clusters, removed clusters, or clusters whose member set changed through added/removed methods. A method body edit by itself is not a cluster-boundary change.

### Existing components
{existing_components}

### Cluster groups to assign
{cfg_clusters}

Your Task:
For each cluster shown above, decide which component it belongs to. Work through the clusters and for each one choose one of two paths:

1. **Route to an existing component.** Reference the component by its exact **component_id** from the list above (e.g. `1.3`). Carry over the component's **name** and a short **description** unchanged, and list the cluster ids that should land here. Multiple groups of clusters can point to the same existing component — that is fine and expected.

   For each routed cluster, also indicate whether a **redetail** pass is needed. Default to yes. Only say no when the change is cosmetic — a refactor, internal rename, small bug fix, or formatting change that leaves the component's high-level purpose untouched. When redetail is not needed, the existing description is preserved as-is and no follow-up pass runs. If you are unsure, lean toward yes.

2. **Create a new component.** Give it a fresh **name** that does not duplicate any existing component, write a **description** explaining what this component does and why these clusters belong together, and choose a **parent_id** — the existing component whose scope most naturally encloses the new one, or leave it null for a root-level component.

Important rules:
- Identity is tracked by **component_id**, not by name. Reusing an existing component's name without pointing to its component_id will create a duplicate — that is wrong. If clusters belong in an existing component, you must reference its **component_id**.
- Route each changed cluster to the most specific owning component. If both a parent and one of its children seem relevant, choose the child only; parent/ancestor ownership is updated deterministically after routing. Do not route a cluster to broad callers, registries, or result models just because they import or use the changed implementation.
- `redetail_needed=False` means the component boundary is unchanged. Do not use a NOOP route to absorb new files, new responsibilities, or clusters that primarily belong to another component.
- Every cluster id listed in the "Cluster groups to assign" section must appear in exactly one entry's **cluster_ids**."""


PLANNING_MESSAGE = """Update one scope of the architecture diagram.

Scope: `{scope_id}` (`root` means the top-level diagram)

Existing components in this scope:
{existing_components}

Changed files:
{changed_files}

Structural cluster diff:
{structural_diff}


Return operations for this scope only.

Rules:
- Keep unchanged clusters out of the operations unless the diff makes the component semantically dirty.
- For modified clusters, preserve the existing owning component shown by its clusters=[...] list; use update_component for that owner instead of moving the cluster to another component.
- For new clusters, decide from the structural diff whether they extend an existing responsibility or introduce a new component; do not infer this from file/package layout alone.
- For reshaped groups, follow overlap counts to keep old cluster ownership stable. Only assign a reshaped new cluster to a different component when the diff proves a real responsibility move.
- Use listGitChanges only when the structural diff is not enough to judge semantic impact.
- Do not reparent existing components. Preserve their current scope; reparenting is not a valid incremental operation.
- Every modified/new/reshaped new-side cluster listed below must appear in exactly one operation's cluster_refs.

Architecture output contract:
- This step plans component boundaries only. Do not define component relations; API surfaces and relations are generated later.
- Preserve an existing component's name, description, and key entities unless its architectural responsibility changed.
- For create_component, provide a clear name and description. Select up to 5 key_entities only when their exact qualified names are available; otherwise leave them empty. Key entities are not synthesized later.
- For update_component, include refreshed name, description, or key_entities only when the component's responsibility changed. An empty key_entities list preserves the current selection.
- For update_component, delete_component, and noop, copy the exact component_id from the existing-components list.
"""


class GeminiFlashPromptFactory(AbstractPromptFactory):
    """Prompt factory for Gemini Flash models."""

    def get_system_message(self) -> str:
        return SYSTEM_MESSAGE

    def get_cluster_grouping_message(self) -> str:
        return CLUSTER_GROUPING_MESSAGE

    def get_final_analysis_message(self) -> str:
        return FINAL_ANALYSIS_MESSAGE

    def get_planner_system_message(self) -> str:
        return PLANNER_SYSTEM_MESSAGE

    def get_expansion_prompt(self) -> str:
        return EXPANSION_PROMPT

    def get_validator_system_message(self) -> str:
        return VALIDATOR_SYSTEM_MESSAGE

    def get_component_validation_component(self) -> str:
        return COMPONENT_VALIDATION_COMPONENT

    def get_relationships_validation(self) -> str:
        return RELATIONSHIPS_VALIDATION

    def get_system_meta_analysis_message(self) -> str:
        return SYSTEM_META_ANALYSIS_MESSAGE

    def get_meta_information_prompt(self) -> str:
        return META_INFORMATION_PROMPT

    def get_file_classification_message(self) -> str:
        return FILE_CLASSIFICATION_MESSAGE

    def get_validation_feedback_message(self) -> str:
        return VALIDATION_FEEDBACK_MESSAGE

    def get_system_details_message(self) -> str:
        return SYSTEM_DETAILS_MESSAGE

    def get_cfg_details_message(self) -> str:
        return CFG_DETAILS_MESSAGE

    def get_details_message(self) -> str:
        return DETAILS_MESSAGE

    def get_incremental_grouping_message(self) -> str:
        return INCREMENTAL_GROUPING_MESSAGE

    def get_planning_message(self) -> str:
        return PLANNING_MESSAGE

    def get_scope_relations_message(self) -> str:
        return SCOPE_RELATIONS_MESSAGE
