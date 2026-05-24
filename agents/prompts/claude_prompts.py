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
3. For each component, specify which named cluster groups it encompasses (use exact group names from the analysis above)
4. Add 2-5 key entities (the most important classes/methods) for each component, mentioning their qualified names and source files
5. Define relationships between components (max 2 per component pair; avoid paired sends/returns where ComponentA sends a message to ComponentB and ComponentB returns the result)
6. Provide a one-paragraph description of the overall main flow and purpose

Guidelines for {project_type} projects:
- Aim for 5-8 final components
- Merge related cluster groups that serve a common purpose
- Each component should have clear boundaries
- Include only architecturally significant relationships

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
3. For each sub-component, specify which named cluster groups it encompasses (use exact group names from the analysis above)
4. Add 2-5 key entities (the most important classes/methods) for each sub-component, mentioning their qualified names and source files
5. Define relationships between sub-components (max 2 per component pair; avoid paired sends/returns where ComponentA sends a message to ComponentB and ComponentB returns the result)
6. Provide a one-paragraph description of the subsystem's main flow and purpose

Guidelines for {project_type} projects:
- Aim for 3-8 final sub-components
- Merge related cluster groups that serve a common purpose
- Each sub-component should have clear boundaries
- Include only architecturally significant relationships

Constraints:
- Focus on subsystem-specific functionality
- Exclude utility/logging sub-components
- Sub-components should translate well to flow diagram representation

Justify component choices based on fundamental architectural importance."""

INCREMENTAL_GROUPING_MESSAGE = """Update the architecture of `{project_name}` by routing changed and new CFG clusters to the right components.

<context>
Project context: {meta_context}

Project Type: {project_type}

The previous analysis established the components below. Most clusters are unchanged and stay where they are; this prompt only shows the slice that changed (new clusters or clusters whose member methods changed).

### Existing components (each line shows `component_id "name"`)
{existing_components}

### Cluster groups to assign
{cfg_clusters}
</context>

<instructions>
Your Task:
Route each cluster shown above into the right component. Every cluster id must appear in exactly one routing entry.

For each cluster, there are two possibilities:

1. **Route to an existing component.** Match the cluster to one of the existing components listed above by its component id (e.g. "1.3"). Reuse that component's name and a short description verbatim, and collect the cluster ids that belong to it. Multiple groups of clusters can route to the same existing component — just make a separate entry for each group.

   Also judge whether the component's description needs updating. Set the **redetail_needed** flag to True (the default) when the cluster delta meaningfully changes what the component does — a new responsibility, a removed responsibility, or a semantic shift in its public surface. Set it to False only when the delta is purely cosmetic — an internal refactor, rename, small bug fix, or formatting change — and the component's high-level purpose is untouched. When False, the existing description is preserved as-is and no follow-up redetail runs. Bias toward True if you are uncertain.

2. **Create a new component.** Leave the existing component id as null, give the new component a fresh name (distinct from every existing component name), write a description paragraph explaining what this component does and why these clusters belong together, and choose a parent component id under which it should attach (or null for root). Pick the parent whose scope most naturally encloses the new component.

A critical correctness rule: identity is tracked by component id, not by name. If clusters belong in an existing component, you MUST reference its component id explicitly. Reusing an existing component's name without pointing to its component id will fork a duplicate — that is wrong.

Focus on:
- Placing clusters where they belong architecturally, guided by method names, call patterns, and the existing component boundaries
- Creating cohesive groupings that reflect the actual {project_type} architecture
- Accurately judging whether a delta is meaningful or cosmetic for the redetail decision
- Choosing sensible parents for new components based on scope and responsibility
</instructions>

<tool_usage_policy>
For each cluster you're uncertain about, you may read source. Keep each read small and targeted — the source of a single representative qname is usually the right unit. Continue reading further (still in small, focused steps) only while you remain uncertain about that specific cluster's placement, and stop as soon as your confidence is high. Don't broaden the scope of a single read to cover ground you don't yet need.
</tool_usage_policy>"""


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

    def get_incremental_grouping_message(self) -> str:
        return INCREMENTAL_GROUPING_MESSAGE

    def get_details_message(self) -> str:
        return DETAILS_MESSAGE
