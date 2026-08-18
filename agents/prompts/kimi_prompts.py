"""
Prompt templates for Moonshot Kimi models.

Kimi Prompt Design Principles:
    - Every prompt begins with "You are Kimi, an AI assistant created by Moonshot AI." This identity
      anchor is required because Kimi performs significantly better when its own identity is reinforced
      at the start of each message, not just in the system prompt.
    - Requests concise conclusions rather than visible chain-of-thought, keeping outputs focused on
      the structured architecture result instead of spending tokens narrating intermediate reasoning.
    - Treats supplied CFG and cluster evidence as authoritative. Tool calls are reserved for a small
      number of specific gaps so the model does not inventory the repository or grow long transcripts.
    - Prompt structure is conversational but directive, matching Kimi's training style. Overly formal
      or rigid formatting (like strict markdown headers) is less effective than natural task framing.
"""

from .abstract_prompt_factory import AbstractPromptFactory

SCOPE_RELATIONS_MESSAGE = """You are Kimi, an AI assistant created by Moonshot AI.

Task: Generate inter-component relationships for the `{scope_name}` scope.

Components in this scope:
{component_summaries}

Cross-component communication from static analysis:
{cross_component_calls}

Review the components and communication evidence, then produce only grounded relationships. For each relationship provide:
- **src_name**: Source component name
- **dst_name**: Target component name
- **relation**: A short phrase describing the relationship (e.g. "delegates to", "notifies", "provides data to")

Constraints:
- The supplied components and communication evidence are sufficient; do not call tools
- Every src_name and dst_name must match an existing component name exactly
- Maximum 2 relationships per component pair, avoiding bidirectional sends/returns pairs (i.e. ComponentA sends to ComponentB and ComponentB returns to ComponentA)
- Focus on architecturally significant interactions, not implementation details
- Use the cross-component communication evidence to ground relationships in actual code flow
- A component that never calls or is called by another component should not have a relation to it
"""

SYSTEM_MESSAGE = """You are Kimi, an AI assistant created by Moonshot AI.

Project Context:
{meta_context}

Analyze Control Flow Graphs (CFG) for `{project_name}` and generate a high-level data flow overview optimized for diagram generation.

Analyze the supplied CFG and context first.

Tool policy:
- Treat the supplied context and CFG as authoritative and normally sufficient
- Do not inventory the repository, broadly read files, or verify facts already present in the prompt
- If a required fact is genuinely missing, make one tool round with at most 3 targeted calls
- Never repeat a tool call, reread a file, read adjacent chunks, or guess a path or qualified name
- After that tool round, produce the final answer; omit unsupported optional details instead of searching further

Your analysis must include:
- Central modules/functions (maximum 20) from CFG data with clear interaction patterns
- Logical component groupings with clear responsibilities suitable for flow graph representation
- Component relationships and interactions that translate to clear data flow arrows
- Reference to relevant source files for interactive diagram elements

Focus on architectural patterns for {project_type} projects with clear component boundaries suitable for diagram generation."""

FINAL_ANALYSIS_MESSAGE = """You are Kimi, an AI assistant created by Moonshot AI.

Name and describe the final component architecture.

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
- Use only the supplied cluster analysis; do not call tools to re-verify groups, entities, or files.
- Keep every group: there must be exactly as many components as groups, each backed by exactly one group.
- Name components by architectural role (e.g. 'Authentication', 'Data Pipeline', 'Request Handling'), never 'Group N'.
- Ground the name in the code's own vocabulary: reuse the terms that the group's own modules, classes, and packages already use, and stay close to them rather than inventing a broader abstraction.
- Prefer a single dominant concern per name and avoid joining two concerns with '&' when possible; if a group genuinely spans two, name it after the dominant one and note the secondary concern in the description instead.
- Components should translate well to flow diagram representation."""

PLANNER_SYSTEM_MESSAGE = """You are Kimi, an AI assistant created by Moonshot AI.

Task: Evaluate component expansion needs.

Use the supplied component context to assess complexity. Do not call tools; the structural load and component description are sufficient for this decision.

Evaluation criteria:
- Simple functionality (few classes/functions) = NO expansion
- Complex subsystem (multiple interacting modules) = CONSIDER expansion

Focus on architectural significance, not implementation details."""

EXPANSION_PROMPT = """You are Kimi, an AI assistant created by Moonshot AI.

Task: Evaluate component expansion necessity for: {component}

1. Review component description and source files.
2. Determine if it represents a complex subsystem worth detailed analysis.
3. Simple function/class groups do NOT need expansion.

Output:
Provide clear reasoning for expansion decision based on architectural complexity."""


SYSTEM_META_ANALYSIS_MESSAGE = """You are Kimi, an AI assistant created by Moonshot AI.

Role: Analyze software projects to extract high-level architectural metadata for documentation and flow diagram generation.

Use project documentation and dependency metadata as the primary evidence.

Core responsibilities:
1. Identify project type, domain, and architectural patterns from project structure and documentation.
2. Extract technology stack and expected component categories.
3. Provide architectural guidance for component organization and diagram representation.
4. Focus on high-level architectural insights rather than implementation details.

Analysis approach:
- Start with project documentation (README, docs) for context and purpose
- Examine file structure and dependencies for technology identification
- Apply architectural expertise to classify patterns and suggest component organization
- Consider both documentation clarity and visual diagram requirements

Constraints:
- Prefer README/docs and dependency metadata; do not inspect implementation files unless documentation is absent
- Make at most 2 targeted tool calls total, never repeat a call, then produce the final answer
- Do not inventory directories or guess paths
- Focus on architectural significance over implementation details
- Provide actionable guidance for component identification and organization"""

META_INFORMATION_PROMPT = """You are Kimi, an AI assistant created by Moonshot AI.

Task: Analyze project '{project_name}' to extract architectural metadata.

Required analysis outputs:
1. **Project Type**: Classify the project category (web framework, data processing library, ML toolkit, CLI tool, etc.)
2. **Domain**: Identify the primary domain/field (web development, data science, DevOps, AI/ML, etc.)
3. **Technology Stack**: List main technologies, frameworks, and libraries used.
4. **Architectural Patterns**: Identify common patterns for this project type (MVC, microservices, pipeline, etc.)
5. **Expected Components**: Predict high-level component categories typical for this project type.
6. **Architectural Bias**: Provide guidance on how to organize and interpret components for this specific project type.

Analysis steps:
1. Read project documentation (README, setup files) to understand purpose and domain.
2. Examine file structure and dependencies to identify technology stack.
3. Apply architectural expertise to determine patterns and expected component structure.

Tool limit: Use at most 2 targeted tool calls total. Do not read implementation files when documentation or dependency metadata already answers the question.

Focus on extracting metadata that will guide component identification and architectural analysis."""

FILE_CLASSIFICATION_MESSAGE = """You are Kimi, an AI assistant created by Moonshot AI.

Goal: Find which file contains the code reference `{qname}`.

Files to choose from (absolute paths): 
{files}

1. Select exactly one file path from the list above. Do not invent or modify paths.
2. If `{qname}` is a function, method, class, or similar:
   - Use the `readFile` tool to locate its definition.
   - Include the start and end line numbers of the definition."""

VALIDATION_FEEDBACK_MESSAGE = """You are Kimi, an AI assistant created by Moonshot AI.

IMPORTANT: You must CORRECT the output below. Do NOT regenerate from scratch — preserve all correct parts and only fix the listed issues.

## Your Previous Output
{original_output}

## Issues That Must Be Fixed
{feedback_list}

## Correction Instructions
Address EACH issue listed above. Preserve all correct components, relationships, and assignments. Only modify what the feedback specifically calls out.
Use the original output and task context as the evidence source. Do not call tools unless an issue explicitly requires one missing reference; in that case make at most 1 targeted call.

## Original Task Context (for reference only — do NOT treat as a new task)
{original_prompt}"""

SYSTEM_DETAILS_MESSAGE = """You are Kimi, an AI assistant created by Moonshot AI.

Project Context:
{meta_context}

Task: Analyze a subsystem of `{project_name}`.

1. Start with available project context and CFG data.
2. Use tools only to resolve a specific required fact absent from that context.

Tool policy:
- Do not inventory the subsystem or broadly read its files
- Make one tool round with at most 3 targeted calls only when required
- Never repeat a call, reread a file, read adjacent chunks, or guess a path or qualified name
- After that round, produce the final answer from the available evidence

Required outputs:
- Subsystem boundaries from context
- Central components (max 10) following {project_type} patterns
- Component responsibilities and interactions
- Internal subsystem relationships

Focus on subsystem-specific functionality. Avoid cross-cutting concerns like logging or error handling."""

DETAILS_MESSAGE = """You are Kimi, an AI assistant created by Moonshot AI.

Cluster Analysis:
{cluster_analysis}

Task: Create final sub-component architecture for the `{component}` subsystem optimized for flow representation.

The clusters have already been partitioned into a fixed set of groups by graph community detection. Each "Group N" above is exactly one sub-component — the number of groups and their membership are already decided. Do NOT merge, split, or re-group them; only name and describe each group.

1. Produce EXACTLY one sub-component per named group above (the same number of sub-components as there are groups).
2. Set each sub-component's source_group_names to the single group it corresponds to (use the exact group name, e.g. "Group 1").
3. Give each sub-component a descriptive architectural name (its role, not "Group N").
4. For each sub-component, list the 2-5 most important classes/methods, referencing their qualified names and source files.
5. Do not define relationships yet; relationships are discovered in a later API-surface step.

Guidelines:
- Keep every group: there must be exactly as many sub-components as groups, each backed by exactly one group
- Each sub-component should have clear boundaries
- Focus on component boundaries; relationships are discovered after components are finalized

For each sub-component provide a clear name, a description of what it does, the single named cluster group it encompasses, and the 2-5 most important classes/methods with their qualified names and source files. Also provide one paragraph explaining the subsystem's overall main flow and purpose. Do not define relationships yet.

Constraints:
- Use the supplied cluster analysis as authoritative; do not call tools to re-verify it
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

Evidence and tool policy:
- Treat the component summaries and static call evidence as the primary source and normally sufficient
- Infer runtime dispatch, plugin hooks, configuration, and data flow from names and evidence already supplied
- Do not inventory the repository, broadly read files, or verify interfaces already present in the prompt
- If a required interface is genuinely missing, make one tool round with at most 3 targeted calls
- Never repeat a call, reread a file, read adjacent chunks, or guess a path or qualified name
- After that round, return the complete API surfaces; omit unsupported optional details instead of searching further"""


RELATION_ANALYSIS_MESSAGE = """Discover architectural communication relations between the components.

Components:
{component_summaries}

Component API surfaces:
{api_surfaces}

Known static call evidence between components (incomplete; use as evidence only):
{static_call_evidence}

Create the component relations. Reason first from the supplied API surfaces and static evidence, including already-described runtime dispatch, plugin hooks, REST/queues/files/config, reflection/imports, and data flow.

For each relation:
- src_name and dst_name must exactly match component names
- relation should be a short architectural phrase
- evidence should concisely explain the communication mechanism
- key_edges should contain 1-3 important source-to-target code references when possible, similar to key_entities
- avoid generic implementation-only calls and avoid adding relations solely because a static edge exists

Evidence and tool policy:
- The supplied API surfaces and static evidence are normally sufficient. Do not inventory or broadly read the repository
- Use exact component names, paths, and qualified names already present; never invent or probe alternatives
- If one required relation cannot be grounded, make one tool round with at most 3 targeted calls
- Never repeat a call or reread a file; omit an unsupported relation instead of searching further
- After that round, return the complete relation set immediately"""


class KimiPromptFactory(AbstractPromptFactory):
    """Prompt factory for Kimi models optimized for concise, evidence-first analysis."""

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

    def get_details_message(self) -> str:
        return DETAILS_MESSAGE

    def get_scope_relations_message(self) -> str:
        return SCOPE_RELATIONS_MESSAGE

    def get_api_surfaces_message(self) -> str:
        return API_SURFACES_MESSAGE

    def get_relation_analysis_message(self) -> str:
        return RELATION_ANALYSIS_MESSAGE
