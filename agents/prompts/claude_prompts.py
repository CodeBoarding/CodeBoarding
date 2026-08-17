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

SCOPE_RELATIONS_MESSAGE = """Generate inter-component relationships for the `{scope_name}` scope.

<context>
### Components in this scope
{component_summaries}

### Cross-component communication from static analysis
{cross_component_calls}
</context>

<instructions>
Review the components and cross-component communication evidence above. Generate `components_relations` entries describing how these components interact.

For each relationship provide:
- **src_name**: Source component name
- **dst_name**: Target component name
- **relation**: Short phrase (e.g. "delegates to", "notifies", "provides data to")

Constraints:
- Every src_name and dst_name MUST match an existing component name exactly
- Maximum 2 relationships per component pair — avoid bidirectional sends/returns pairs
- Focus on architecturally significant interactions, not implementation details
- Ground relationships in the cross-component communication evidence
- A component that never calls or is called by another component should not have a relation to it
</instructions>

<thinking>
Map the cross-component call evidence to the component boundaries first. Then identify which pairs have meaningful architectural interactions worth documenting. Discard pairs with no communication evidence.
</thinking>"""

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

FINAL_ANALYSIS_MESSAGE = """Name and describe the final component architecture.

The clusters have already been partitioned into a fixed set of groups by graph community detection. Each "Group N" below is exactly one top-level component — the number of groups and their membership are already decided. Do NOT merge, split, or re-group them; only name and describe each group.

Cluster Analysis:
{cluster_analysis}

Instructions:
1. Produce EXACTLY one component per named group above (the same number of components as there are groups).
2. Set each component's source_group_names to the single group it corresponds to (use the exact group name, e.g. "Group 1").
3. Give each component a descriptive architectural name (its role, not "Group N") and a one-sentence description of what it does.
4. Add 2-5 key entities (the most important classes/methods) per component, using their exact qualified names and source files.
5. Do not define relationships yet; relationships are discovered in a later API-surface step.
6. Provide a one-paragraph description of the overall main flow and purpose.

Constraints:
- Keep every group: there must be exactly as many components as groups, each backed by exactly one group.
- Name components by architectural role (e.g. 'Authentication', 'Data Pipeline', 'Request Handling'), never 'Group N'.
- Ground the name in the code's own vocabulary: reuse the terms that the group's own modules, classes, and packages already use, and stay close to them rather than inventing a broader abstraction.
- Prefer a single dominant concern per name and avoid joining two concerns with '&' when possible; if a group genuinely spans two, name it after the dominant one and note the secondary concern in the description instead.
- Components should translate well to flow diagram representation."""

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

DETAILS_MESSAGE = """Create final sub-component architecture for the `{component}` subsystem optimized for flow representation.

The clusters have already been partitioned into a fixed set of groups by graph community detection. Each "Group N" below is exactly one sub-component — the number of groups and their membership are already decided. Do NOT merge, split, or re-group them; only name and describe each group.

Cluster Analysis:
{cluster_analysis}

Instructions:
1. Produce EXACTLY one sub-component per named group above (the same number of sub-components as there are groups)
2. Set each sub-component's source_group_names to the single group it corresponds to (use the exact group name, e.g. "Group 1")
3. Give each sub-component a descriptive architectural name (its role, not "Group N") and a one-sentence description of what it does
4. Add 2-5 key entities (the most important classes/methods) for each sub-component, mentioning their qualified names and source files
5. Do not define relationships yet; relationships are discovered in a later API-surface step
6. Provide a one-paragraph description of the subsystem's main flow and purpose

Guidelines:
- Keep every group: there must be exactly as many sub-components as groups, each backed by exactly one group
- Each sub-component should have clear boundaries
- Focus on component boundaries; relationships are discovered after components are finalized

Constraints:
- Focus on subsystem-specific functionality
- Exclude utility/logging sub-components
- Sub-components should translate well to flow diagram representation

Justify component choices based on fundamental architectural importance."""


API_SURFACES_MESSAGE = """Analyze the component API surfaces.

Components:
{component_summaries}

Known static call evidence between components (incomplete; do not treat as the full communication model):
{static_call_evidence}

Identify each component's API surface. For every component, describe:
- provided_interfaces: important methods/classes/config symbols it exposes or uses as entrypoints
- consumed_interfaces: important methods/classes/config symbols it calls, configures, imports, or expects from others
- incoming_api_paths and outgoing_api_paths: how other components enter this component's API, and how this component reaches other components' APIs; include direct calls, runtime dispatch, plugin hooks, REST, queues, files, config, reflection/import, subprocesses, etc.

Static call evidence is incomplete. Reason from component APIs, registries, protocols, runtime dispatch, plugin hooks, configuration, and data flow."""


RELATION_ANALYSIS_MESSAGE = """Discover architectural communication relations between the components.

Components:
{component_summaries}

Component API surfaces:
{api_surfaces}

Known static call evidence between components (incomplete; use as evidence only):
{static_call_evidence}

Create the component relations. Do not limit relations to static calls. First reason from component API surfaces, including runtime dispatch, plugin hooks, REST/queues/files/config, reflection/imports, and data flow. Use static calls only as one evidence source.

For each relation:
- src_name and dst_name must exactly match component names
- relation should be a short architectural phrase
- evidence should concisely explain the communication mechanism
- key_edges should contain 1-3 important source-to-target code references when possible, similar to key_entities
- avoid generic implementation-only calls and avoid adding relations solely because a static edge exists"""


class ClaudePromptFactory(AbstractPromptFactory):
    """Prompt factory for Claude models."""

    def get_system_message(self) -> str:
        return SYSTEM_MESSAGE

    def get_final_analysis_message(self) -> str:
        return FINAL_ANALYSIS_MESSAGE

    def get_planner_system_message(self) -> str:
        return PLANNER_SYSTEM_MESSAGE

    def get_expansion_prompt(self) -> str:
        return EXPANSION_PROMPT

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

    def get_scope_relations_message(self) -> str:
        return SCOPE_RELATIONS_MESSAGE

    def get_details_message(self) -> str:
        return DETAILS_MESSAGE

    def get_api_surfaces_message(self) -> str:
        return API_SURFACES_MESSAGE

    def get_relation_analysis_message(self) -> str:
        return RELATION_ANALYSIS_MESSAGE
