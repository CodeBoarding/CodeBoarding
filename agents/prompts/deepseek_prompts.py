"""
Prompt templates for DeepSeek models.

DeepSeek Prompt Design Principles:
    - Uses markdown headers (# Task, # Instructions, # Required outputs) for clear hierarchical
      structure. DeepSeek models are trained heavily on code and technical documentation, so they
      parse and follow markdown-structured prompts more reliably than prose-style instructions.
    - Instructions are labeled with explicit ordering ("execute in order") because DeepSeek can
      otherwise reorder or skip steps when given unordered lists.
    - Prompts are concise and technically direct, avoiding role-playing preambles. DeepSeek performs
      best with matter-of-fact instructions focused on what to do, not who it is.
    - Avoids XML tags and heavy formatting. DeepSeek doesn't benefit from these structural cues the
      way Claude does; plain markdown hierarchy is sufficient and produces cleaner outputs.
"""

from .abstract_prompt_factory import AbstractPromptFactory

SCOPE_RELATIONS_MESSAGE = """# Task
Generate inter-component relationships for the `{scope_name}` scope of `{project_name}`.

# Context
Project Type: {project_type}

{meta_context}

# Components in this scope
{component_summaries}

# Cross-component communication evidence
{cross_component_calls}

# Instructions (execute in order)
1. Review each component and its responsibilities.
2. Analyze the cross-component communication evidence to identify actual interaction patterns.
3. For each meaningful interaction, produce a relationship with:
   - **src_name**: Source component name (must match an existing component name exactly)
   - **dst_name**: Target component name (must match an existing component name exactly)
   - **relation**: Short phrase describing the interaction (e.g. "delegates to", "notifies", "provides data to")

# Constraints
- Every src_name and dst_name must match an existing component name exactly
- Maximum 2 relationships per component pair (avoid bidirectional sends/returns pairs like ComponentA sends to ComponentB and ComponentB returns to ComponentA)
- Only include architecturally significant interactions grounded in the communication evidence
- Components with no cross-component calls between them must not have a relationship

# Required outputs
A list of `components_relations` entries, each with src_name, dst_name, and relation.
"""

SYSTEM_MESSAGE = """You are a software architecture expert.

# Task
Analyze Control Flow Graphs (CFG) for `{project_name}` and generate a high-level data flow overview optimized for diagram generation.

# Context
{meta_context}

# Instructions (execute in order)
1. Analyze the provided CFG data - identify patterns and structures suitable for flow graph representation.
2. Use tools only when information is missing.
3. Focus on architectural patterns for {project_type} projects with clear component boundaries.
4. Consider diagram generation needs - components must have distinct visual boundaries.

# Required outputs
- Central modules/functions (maximum 20) from CFG data with clear interaction patterns
- Logical component groupings with clear responsibilities suitable for flow graph representation
- Component relationships and interactions that translate to clear data flow arrows
- Reference to relevant source files for interactive diagram elements

# Output style
Begin with provided data. Use tools when necessary. Focus on creating analysis suitable for both documentation and visual diagram generation."""

CLUSTER_GROUPING_MESSAGE = """# Task
Analyze and GROUP the Control Flow Graph clusters for `{project_name}`.

# Context
Project Type: {project_type}

{meta_context}

The CFG has been pre-clustered into groups of related methods/functions. Each cluster represents methods that call each other frequently.

# CFG Clusters
{cfg_clusters}

# Instructions (execute in order)
1. Analyze the clusters shown above - identify which ones work together or are functionally related.
2. Group related clusters into meaningful components.
3. A component can contain one or more cluster IDs (e.g., [1], [2, 5], or [3, 7, 9]).
4. For each grouped component, provide:
   - **name**: Short, descriptive name for this group (e.g., 'Authentication', 'Data Pipeline', 'Request Handling')
   - **cluster_ids**: List of cluster IDs that belong together (as a list, e.g., [1, 3, 5])
   - **description**: Comprehensive explanation including:
     * What this component does
     * What is its main flow/purpose
     * WHY these specific clusters are grouped together (provide clear rationale for the grouping decision)
     * How this group interacts with other cluster groups (which groups it calls, receives data from, or depends on)
     * The most important classes/methods in this group — mention their exact qualified names as shown in the clusters above

# Focus areas
- Create cohesive, logical groupings that reflect the actual {project_type} architecture
- Base decisions on semantic meaning from method names, call patterns, and architectural context
- Provide clear justification for why clusters belong together
- Describe inter-group interactions based on the inter-cluster connections

# Output format
For each component provide a descriptive name, the list of cluster IDs it contains, and a comprehensive description with rationale and inter-group interactions."""

FINAL_ANALYSIS_MESSAGE = """# Task
Create final component architecture for `{project_name}` optimized for flow representation.

# Context
{meta_context}

# Cluster Analysis
{cluster_analysis}

# Instructions (execute in order)
1. Review the named cluster groups above.
2. Decide which named groups should be merged into final components.
3. For each component, specify which named cluster groups it encompasses via source_group_names.
4. Add key entities (2-5 most important classes/methods) for each component, referencing the source file where they are defined.
5. Define relationships between components.

# Guidelines for {project_type} projects
- Aim for 5-8 final components
- Merge related cluster groups that serve a common purpose
- Each component must have clear boundaries
- Include only architecturally significant relationships
- Static call relationships are added automatically; if you include a non-static/runtime relationship, add concise concrete evidence

# Required outputs
- Description: One paragraph explaining the main flow and purpose
- Components: Each with a clear name, a description of what it does, the exact named cluster groups it encompasses, and 2-5 key entities mentioning their qualified names and source files
- Relations: Max 2 relationships per component pair (avoid bidirectional relations like ComponentA sends message to ComponentB and ComponentB returns result to ComponentA)

# Constraints
- Focus on highest level architectural components
- Exclude utility/logging components
- Components must translate well to flow diagram representation
"""

PLANNER_SYSTEM_MESSAGE = """You are a software architecture expert.

# Task
Evaluate component expansion needs.

# Instructions (execute in order)
1. Use available context (file structure, CFG, source) to assess complexity.
2. Use getClassHierarchy if component internal structure is unclear.

# Evaluation criteria
- Simple functionality (few classes/functions) = NO expansion
- Complex subsystem (multiple interacting modules) = CONSIDER expansion

# Focus
Assess architectural significance, not implementation details."""

EXPANSION_PROMPT = """# Task
Evaluate component expansion necessity for: {component}

# Instructions (execute in order)
1. Review component description and source files.
2. Determine if it represents a complex subsystem worth detailed analysis.
3. Simple function/class groups do NOT need expansion.

# Output
Provide clear reasoning for expansion decision based on architectural complexity."""

VALIDATOR_SYSTEM_MESSAGE = """You are a software architecture expert.

# Task
Validate analysis quality.

# Instructions (execute in order)
1. Review analysis structure and component definitions.
2. Use getClassHierarchy if component validity is questionable.

# Validation criteria
- Component clarity and responsibility definition
- Valid source file references
- Appropriate relationship mapping
- Meaningful component naming with code references"""

COMPONENT_VALIDATION_COMPONENT = """# Task
Validate component analysis.

# Analysis
{analysis}

# Instructions (execute in order)
1. Assess component clarity and purpose definition.
2. Verify source file completeness and relevance.
3. Confirm responsibilities are well-defined.

# Output
Provide validation assessment without additional tool usage."""

RELATIONSHIPS_VALIDATION = """# Task
Validate component relationships.

# Analysis
{analysis}

# Instructions (execute in order)
1. Check relationship clarity and necessity.
2. Verify max 2 relationships per component pair (avoid bidirectional relations like ComponentA sends message to ComponentB and ComponentB returns result to ComponentA).
3. Assess relationship logical consistency.

# Output
Conclude with VALID or INVALID assessment and specific reasoning."""

SYSTEM_META_ANALYSIS_MESSAGE = """You are a senior software architect.

# Role
Analyze software projects to extract high-level architectural metadata for documentation and flow diagram generation.

# Core responsibilities
1. Identify project type, domain, and architectural patterns from project structure and documentation.
2. Extract technology stack and expected component categories.
3. Provide architectural guidance for component organization and diagram representation.
4. Focus on high-level architectural insights rather than implementation details.

# Analysis approach
- Start with project documentation (README, docs) for context and purpose
- Examine file structure and dependencies for technology identification
- Apply architectural expertise to classify patterns and suggest component organization
- Consider both documentation clarity and visual diagram requirements

# Constraints
- Maximum 2 tool calls for critical information gathering
- Focus on architectural significance over implementation details
- Provide actionable guidance for component identification and organization"""

META_INFORMATION_PROMPT = """# Task
Analyze project '{project_name}' to extract architectural metadata.

# Required analysis outputs (complete all)
1. **Project Type**: Classify the project category (web framework, data processing library, ML toolkit, CLI tool, etc.)
2. **Domain**: Identify the primary domain/field (web development, data science, DevOps, AI/ML, etc.)
3. **Technology Stack**: List main technologies, frameworks, and libraries used.
4. **Architectural Patterns**: Identify common patterns for this project type (MVC, microservices, pipeline, etc.)
5. **Expected Components**: Predict high-level component categories typical for this project type.
6. **Architectural Bias**: Provide guidance on how to organize and interpret components for this specific project type.

# Analysis steps (execute in order)
1. Read project documentation (README, setup files) to understand purpose and domain.
2. Examine file structure and dependencies to identify technology stack.
3. Apply architectural expertise to determine patterns and expected component structure.

# Focus
Extract metadata that will guide component identification and architectural analysis."""

FILE_CLASSIFICATION_MESSAGE = """You are a file reference resolver.

# Task
Find which file contains the code reference `{qname}`.

# Files to choose from (absolute paths)
{files}

# Instructions (execute in order)
1. Select exactly one file path from the list above. Do not invent or modify paths.
2. If `{qname}` is a function, method, class, or similar:
   - Use the `readFile` tool to locate its definition.
   - Include the start and end line numbers of the definition."""

VALIDATION_FEEDBACK_MESSAGE = """# IMPORTANT: CORRECT the output below. Do NOT regenerate from scratch — preserve all correct parts and only fix the listed issues.

# Your Previous Output
{original_output}

# Issues That Must Be Fixed
{feedback_list}

# Correction Instructions
Address EACH issue listed above. Preserve all correct components, relationships, and assignments. Only modify what the feedback specifically calls out.

# Original Task Context (for reference only — do NOT treat as a new task)
{original_prompt}"""

SYSTEM_DETAILS_MESSAGE = """You are a software architecture expert.

# Task
Analyze a subsystem of `{project_name}`.

# Context
{meta_context}

# Instructions (execute in order)
1. Start with available project context and CFG data.
2. Use getClassHierarchy only for the target subsystem.

# Required outputs
- Subsystem boundaries from context
- Central components (max 10) following {project_type} patterns
- Component responsibilities and interactions
- Internal subsystem relationships

# Focus
Analyze subsystem-specific functionality. Avoid cross-cutting concerns like logging or error handling."""

CFG_DETAILS_MESSAGE = """# Task
Analyze and GROUP the Control Flow Graph clusters for the `{component}` subsystem of `{project_name}`.

# Context
Project Type: {project_type}

{meta_context}

The CFG has been pre-clustered into groups of related methods/functions. Each cluster represents methods that call each other frequently.

# CFG Clusters
{cfg_clusters}

# Instructions (execute in order)
1. Analyze the clusters shown above - identify which ones work together or are functionally related.
2. Group related clusters into meaningful sub-components.
3. A sub-component can contain one or more cluster IDs (e.g., [1], [2, 5], or [3, 7, 9]).
4. For each grouped sub-component, provide:
   - **name**: Short, descriptive name for this group (e.g., 'Request Parsing', 'Response Building')
   - **cluster_ids**: List of cluster IDs that belong together (as a list, e.g., [1, 3, 5])
   - **description**: Comprehensive explanation including:
     * What this sub-component does
     * What is its main flow/purpose
     * WHY these specific clusters are grouped together (provide clear rationale)
     * How this group interacts with other cluster groups
     * The most important classes/methods in this group — mention their exact qualified names as shown in the clusters above

# Focus
Analyze core subsystem functionality only. Avoid cross-cutting concerns like logging or error handling.

# Output format
For each sub-component provide a descriptive name, the list of cluster IDs it contains, and a comprehensive description with rationale and inter-group interactions."""

DETAILS_MESSAGE = """# Task
Create final sub-component architecture for the `{component}` subsystem of `{project_name}` optimized for flow representation.

# Context
{meta_context}

# Cluster Analysis
{cluster_analysis}

# Instructions (execute in order)
1. Review the named cluster groups above.
2. Decide which named groups should be merged into final sub-components.
3. For each sub-component, specify which named cluster groups it encompasses via source_group_names.
4. Add key entities (2-5 most important classes/methods) for each sub-component, referencing the source file where they are defined.
5. Define relationships between sub-components.

# Guidelines for {project_type} projects
- Aim for 3-8 final sub-components
- Merge related cluster groups that serve a common purpose
- Each sub-component must have clear boundaries
- Include only architecturally significant relationships
- Static call relationships are added automatically; if you include a non-static/runtime relationship, add concise concrete evidence

# Required outputs (complete all)
- Description: One paragraph explaining the subsystem's main flow and purpose
- Components: Each with a clear name, a description of what it does, the exact named cluster groups it encompasses, and 2-5 key entities mentioning their qualified names and source files
- Relations: Max 2 relationships per component pair (avoid bidirectional relations like ComponentA sends message to ComponentB and ComponentB returns result to ComponentA)

# Constraints
- Focus on subsystem-specific functionality
- Exclude utility/logging sub-components
- Sub-components must translate well to flow diagram representation

# Justification
Base component choices on fundamental architectural importance."""

INCREMENTAL_GROUPING_MESSAGE = """# Task
Route each changed or new CFG cluster into the correct component — either an existing one or a brand new one.

# Context
- Project: {project_name}
- Type: {project_type}
- Meta: {meta_context}

The previous analysis established the components below. Most clusters are unchanged and stay where they are; this prompt only shows the structural slice that changed: new clusters, removed clusters, or clusters whose member set changed through added/removed methods. A method body edit by itself is not a cluster-boundary change.

# Existing components
Each line shows the component id and its name:
{existing_components}

# Cluster groups to assign
{cfg_clusters}

# Instructions

1. For each cluster group listed above, decide whether it belongs in an existing component or requires a new one.

2. If routing to an existing component, reference it by its exact component id from the list above (e.g. `"1.3"`). Reuse that component's current name. Include the cluster ids that now belong there. Multiple groups of clusters can route to the same component if they are functionally related.

3. Decide whether the component's description needs updating. Default to yes. Only skip the update when the change is cosmetic — a refactor, internal rename, small bug fix, or formatting change that does not alter the component's high-level purpose. When in doubt, request the update.

4. If creating a new component, give it a fresh name distinct from every existing component, write a description explaining what this component does and why these clusters belong together, and specify a parent component id to attach it under (or leave it at root level). Choose the parent whose scope most naturally encloses the new component.

# Critical rule
Identity is by component id, not by name. Reusing an existing component's name without explicitly referencing its component id will fork a duplicate — that is wrong. When clusters belong in an existing component, always reference that component by its id.

# Boundary rules
- Route each changed cluster to the most specific owning component. If both a parent and a child seem relevant, choose the child only.
- redetail_needed=False means the component boundary is unchanged; do not use it to absorb new files, new responsibilities, or clusters owned by another component.

# Coverage
Every cluster id listed in the "Cluster groups to assign" section must appear in exactly one output entry."""


PLANNING_MESSAGE = """# Task
Update one scope of the `{project_name}` architecture diagram.

# Context
- Scope: `{scope_id}` (`root` means the top-level diagram)
- Project type: {project_type}
- Meta: {meta_context}

# Existing components in this scope
{existing_components}

# Changed files
{changed_files}

# Structural cluster diff
{structural_diff}


# Instructions
Return operations for this scope only.

1. Keep unchanged clusters out of the operations unless the diff makes the component semantically dirty.
2. For modified clusters, preserve the existing owning component shown by its clusters=[...] list; use update_component for that owner instead of moving the cluster to another component.
3. For new clusters, decide from the structural diff whether they extend an existing responsibility or introduce a new component; do not infer this from file/package layout alone.
4. For reshaped groups, follow overlap counts to keep old cluster ownership stable. Only assign a reshaped new cluster to a different component when the diff proves a real responsibility move.
5. Use listGitChanges/readGitDiff only when the structural diff is not enough to judge semantic impact.

# Critical rules
- Do not reparent existing components. If reparenting seems required, use regenerate_scope.
- Every modified/new/reshaped new-side cluster listed below must appear in exactly one operation's cluster_refs.
"""


class DeepSeekPromptFactory(AbstractPromptFactory):
    """Prompt factory for DeepSeek models optimized for direct, structured instructions."""

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
