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

# Focus areas
- Create cohesive, logical groupings that reflect the actual {project_type} architecture
- Base decisions on semantic meaning from method names, call patterns, and architectural context
- Provide clear justification for why clusters belong together
- Describe inter-group interactions based on the inter-cluster connections

# Output format
Return a ClusterAnalysis with cluster_components using ClustersComponent model.
Each component must have name (descriptive label), cluster_ids (list), and description (comprehensive explanation with rationale and inter-group interactions)."""

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
4. Add key entities (2-5 most important classes/methods) for each component using SourceCodeReference.
5. Define relationships between components.

# Guidelines for {project_type} projects
- Aim for 5-8 final components
- Merge related cluster groups that serve a common purpose
- Each component must have clear boundaries
- Include only architecturally significant relationships

# Required outputs
- Description: One paragraph explaining the main flow and purpose
- Components: Each with:
  * name: Clear component name
  * description: What this component does
  * source_group_names: Which named cluster groups from the analysis above this component encompasses (use exact group names)
  * key_entities: 2-5 most important classes/methods (SourceCodeReference objects with qualified_name and reference_file)
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

# Focus
Analyze core subsystem functionality only. Avoid cross-cutting concerns like logging or error handling.

# Output format
Return a ClusterAnalysis with cluster_components using ClustersComponent model.
Each component must have name (descriptive label), cluster_ids (list), and description (comprehensive explanation with rationale and inter-group interactions)."""

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
4. Add key entities (2-5 most important classes/methods) for each sub-component using SourceCodeReference.
5. Define relationships between sub-components.

# Guidelines for {project_type} projects
- Aim for 3-8 final sub-components
- Merge related cluster groups that serve a common purpose
- Each sub-component must have clear boundaries
- Include only architecturally significant relationships

# Required outputs (complete all)
- Description: One paragraph explaining the subsystem's main flow and purpose
- Components: Each with:
  * name: Clear sub-component name
  * description: What this sub-component does
  * source_group_names: Which named cluster groups from the analysis above this sub-component encompasses (use exact group names)
  * key_entities: 2-5 most important classes/methods (SourceCodeReference objects with qualified_name and reference_file)
- Relations: Max 2 relationships per component pair (avoid bidirectional relations like ComponentA sends message to ComponentB and ComponentB returns result to ComponentA)

# Constraints
- Focus on subsystem-specific functionality
- Exclude utility/logging sub-components
- Sub-components must translate well to flow diagram representation

# Justification
Base component choices on fundamental architectural importance."""


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
