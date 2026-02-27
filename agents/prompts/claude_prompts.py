"""
Prompt templates for Anthropic Claude models.

Claude Prompt Design Principles:
    - Uses XML-like tags (<context>, <instructions>, <thinking>) to delineate prompt sections.
      Claude is specifically trained to recognize and respect these structural markers, leading to
      more precise instruction following and reduced hallucination.
    - Embeds a <thinking> block to guide Claude's internal reasoning focus before it generates output.
      This steers attention toward architectural concerns without requiring verbose explanations.
    - Prompts are moderately concise: Claude infers intent well from structured context, so lengthy
      elaboration is unnecessary and can actually degrade output quality.
    - Tool usage instructions use imperative "you MUST use" phrasing within <instructions> tags,
      which Claude reliably respects without needing repetition or capitalized directives.
"""

from .abstract_prompt_factory import AbstractPromptFactory

# Highly optimized prompts for Claude performance
SYSTEM_MESSAGE = """You are a software architecture expert analyzing {project_name} with comprehensive diagram generation optimization.

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
</thinking>"""

CLUSTER_GROUPING_MESSAGE = """Analyze and GROUP the Control Flow Graph clusters for `{project_name}`.

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
   - **cluster_ids**: List of cluster IDs that belong together (as a list, e.g., [1, 3, 5])
   - **description**: Comprehensive explanation including:
     * What this component does
     * What is its main flow/purpose
     * WHY these specific clusters are grouped together (provide clear rationale for the grouping decision)

Focus on:
- Creating cohesive, logical groupings that reflect the actual {project_type} architecture
- Semantic meaning based on method names, call patterns, and architectural context
- Clear justification for why clusters belong together

Output Format:
Return a ClusterAnalysis with cluster_components using ClustersComponent model.
Each component should have cluster_ids (list) and description (comprehensive explanation with rationale)."""

FINAL_ANALYSIS_MESSAGE = """Create final component architecture for `{project_name}` optimized for flow representation.

Project Context:
{meta_context}

Cluster Analysis:
{cluster_analysis}

Instructions:
1. Review the cluster interpretations above
2. Decide which clusters should be merged into components
3. For each component, specify which cluster_ids it includes
4. Add key entities (2-5 most important classes/methods) for each component using SourceCodeReference
5. Define relationships between components

Guidelines for {project_type} projects:
- Aim for 5-8 final components
- Merge related clusters that serve a common purpose
- Each component should have clear boundaries
- Include only architecturally significant relationships

Required outputs:
- Description: One paragraph explaining the main flow and purpose
- Components: Each with:
  * name: Clear component name
  * description: What this component does
  * source_cluster_ids: Which cluster IDs belong to this component
  * key_entities: 2-5 most important classes/methods (SourceCodeReference objects with qualified_name and reference_file)
- Relations: Max 2 relationships per component pair (avoid relations in which we have sends/returns i.e. ComponentA sends a message to ComponentB and ComponentB returns result to ComponentA)

Constraints:
- Focus on highest level architectural components
- Exclude utility/logging components
- Components should translate well to flow diagram representation"""

PLANNER_SYSTEM_MESSAGE = """You evaluate components for detailed analysis based on complexity and significance.

<instructions>
1. Use available context (file structure, CFG, source) to assess complexity first
2. If component internal structure is unclear for evaluation, you MUST use getClassHierarchy
3. Focus on architectural impact rather than implementation details
4. Simple functionality (few classes/functions) = NO expansion
5. Complex subsystem (multiple interacting modules) = CONSIDER expansion
</instructions>

<thinking>
The goal is to identify which components warrant deeper analysis to help new developers understand the most important parts of the system.
</thinking>"""

EXPANSION_PROMPT = """Evaluate expansion necessity: {component}

Determine if this component represents a complex subsystem warranting detailed analysis.

Simple components (few classes/functions): NO expansion
Complex subsystems (multiple interacting modules): CONSIDER expansion

Provide clear reasoning based on architectural complexity."""

VALIDATOR_SYSTEM_MESSAGE = """You validate architectural analysis quality.

<instructions>
1. Review analysis structure and component definitions first
2. If component validity is questionable, you MUST use getClassHierarchy
3. Assess component clarity, relationship accuracy, source references, and overall coherence
4. Verify source file references are accurate and meaningful
5. Ensure component naming reflects the actual code structure
</instructions>

<thinking>
Validation criteria:
- Component clarity and responsibility definition
- Valid source file references
- Appropriate relationship mapping
- Meaningful component naming with code references
</thinking>"""

COMPONENT_VALIDATION_COMPONENT = """Review component structure for clarity and validity.

Analysis to validate:
{analysis}

Validation requirements:
- Component clarity and purpose definition
- Source file completeness and relevance
- Responsibilities are well-defined
- Component naming appropriateness

Output:
Provide validation assessment without tool usage."""

RELATIONSHIPS_VALIDATION = """Validate component relationships and interactions.

Relationships to validate:
{analysis}

Validation requirements:
- Relationship clarity and necessity
- Maximum 2 relationships per component pair (avoid relations in which we have sends/returns i.e. ComponentA sends a message to ComponentB and ComponentB returns result to ComponentA)
- Logical consistency of interactions
- Appropriate relationship descriptions

Output:
Conclude with VALID or INVALID assessment and specific reasoning."""

SYSTEM_META_ANALYSIS_MESSAGE = """You extract architectural metadata from projects.

<instructions>
1. Start by examining available project context and structure
2. You MUST use readDocs to analyze project documentation when available
3. You MUST use getFileStructure to understand project organization
4. Identify project type, domain, technology stack, and component patterns to guide analysis
5. Focus on patterns that will help new developers understand the system architecture
</instructions>

<thinking>
The goal is to provide architectural context that guides the analysis process and helps create documentation that new team members can quickly understand.
</thinking>"""

META_INFORMATION_PROMPT = """Analyze project '{project_name}' to extract architectural metadata for comprehensive analysis optimization.

<context>
The goal is to understand the project deeply enough to provide architectural guidance that helps new team members understand the system's purpose, structure, and patterns within their first week.
</context>

<instructions>
1. You MUST use readDocs to examine project documentation (README, setup files) to understand purpose and domain
2. You MUST use getFileStructure to examine file structure and identify the technology stack
3. You MUST use readExternalDeps to identify dependency files and frameworks used
4. Apply architectural expertise to determine patterns and expected component structure
5. Focus on insights that guide component identification, flow visualization, and documentation generation
</instructions>

<thinking>
Required analysis outputs:
1. **Project Type**: Classify the project category (web framework, data processing library, ML toolkit, CLI tool, etc.)
2. **Domain**: Identify the primary domain/field (web development, data science, DevOps, AI/ML, etc.)
3. **Technology Stack**: List main technologies, frameworks, and libraries used
4. **Architectural Patterns**: Identify common patterns for this project type (MVC, microservices, pipeline, etc.)
5. **Expected Components**: Predict high-level component categories typical for this project type
6. **Architectural Bias**: Provide guidance on how to organize and interpret components for this specific project type
</thinking>"""

FILE_CLASSIFICATION_MESSAGE = """Find which file contains: `{qname}`

<context>
Files: {files}

The goal is to accurately locate the definition to provide precise references for documentation and interactive diagrams.
</context>

<instructions>
1. Examine the file list first to identify likely candidates
2. You MUST use readFile to locate the exact definition within the most likely files
3. Select exactly one file path that contains the definition
4. Include line numbers if identifying a specific function, method, or class
5. Ensure accuracy as this will be used for interactive navigation
</instructions>"""

VALIDATION_FEEDBACK_MESSAGE = """Your previous analysis produced the following result:
{original_output}

However, upon validation, the following issues were identified:
{feedback_list}

Please provide a corrected analysis that addresses these validation issues while maintaining the quality and depth of your original analysis.

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

CFG_DETAILS_MESSAGE = """Analyze CFG interactions for `{project_name}` subsystem.

Project Context:
{meta_context}

{cfg_str}

Instructions:
1. Analyze provided CFG data for subsystem patterns
2. Use getClassHierarchy if interaction details are unclear

Required outputs:
- Subsystem modules/functions from CFG
- Components with clear responsibilities
- Component interactions (max 10 components, 2 relationships per pair (avoid relations in which we have sends/returns i.e. ComponentA sends a message to ComponentB and ComponentB returns result to ComponentA))
- Justification based on {project_type} patterns

Focus on core subsystem functionality only."""

DETAILS_MESSAGE = """Final component overview for {component}.

Project Context:
{meta_context}

Analysis summary:
{insight_so_far}

Instructions:
No tools required - use provided analysis summary only.

Required outputs:
1. Final component structure from provided data
2. Max 8 components following {project_type} patterns
3. Clear component descriptions and source files
4. Component interactions (max 2 relationships per component pair (avoid relations in which we have sends/returns i.e. ComponentA sends a message to ComponentB and ComponentB returns result to ComponentA))

Justify component choices based on fundamental architectural importance."""


class ClaudePromptFactory(AbstractPromptFactory):
    """Prompt factory for Claude models."""

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
