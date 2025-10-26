from .abstract_prompt_factory import AbstractPromptFactory

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

CFG_MESSAGE = """Analyze the Control Flow Graph for `{project_name}` with diagram generation in mind.

Project Context:
{meta_context}

The Control-Flow data is represented in the following format, firstly we have clustered methods, which are closely related to each other and then we have the edges between them.
As not all methods are clustered, some methods are not part of any cluster. These methods are represented as single nodes and are also added to the graph and are listed below the clusters.
Control-flow Data:
{cfg_str}

Instructions:
1. Analyze the provided CFG data first, identifying clear component boundaries for a flow graph representation
2. Use getPackageDependencies to get information on package structure for diagram organization
3. Use getClassHierarchy if component relationships need clarification for arrow connections

Required outputs:
- Important modules/functions from CFG data with clear interaction pathways
- Abstract components (max 15) with clear names, descriptions, and responsibilities
- Component relationships (max 2 per component pair) suitable for diagram arrows
- Source file references from CFG data for interactive click events

Apply {project_type} architectural patterns. Focus on core business logic with clear data flow - exclude logging/error handling components that clutter diagrams."""

SOURCE_MESSAGE = """Validate and enhance component analysis using source code for comprehensive documentation and diagram generation.

Project Context:
{meta_context}

Current analysis:
{insight_so_far}

Instructions:
1. Review current analysis to identify gaps and optimize for flow graph representation
2. Use getClassHierarchy if component structure needs clarification for diagram mapping
3. Use getSourceCode to ensure components have clear source file references for click events
4. Use readFile for full file references when component boundaries need validation
5. Use getFileStructure for directory/package references - these create better high-level diagram components

Required outputs:
- Validated component boundaries optimized for both analysis and diagram representation
- Refined components (max 10) using {project_type} patterns with distinct responsibilities
- Confirmed component relationships suitable for clear diagram connections
- Clear source file references for interactive diagram elements and documentation
- Component groupings that translate well to diagram subgraphs and documentation sections

Work primarily with existing insights. Use tools for missing information. Consider both documentation clarity and flow graph representation."""

CLASSIFICATION_MESSAGE = """You are the software architecture expert, you have analysed the project {project_name} and have identified the following components:
{components}

Now you have to classify each of the file projects into one of the components. Here is a batch of unclassified files:
{files}

You can use the readFile tool to read the file content if you need more information to classify the file correctly. Please take a look at the file content before making a decision especially if you don't find it relevant to the already related files in the component.
If you can't find a relevant component for the file, classify it as part of the "Unclassified" component.
"""

CONCLUSIVE_ANALYSIS_MESSAGE = """Final architecture analysis for `{project_name}` optimized for representing the flow.

Project Context:
{meta_context}

CFG Analysis:
{cfg_insight}

Source Analysis:
{source_insight}

Instructions:
Use provided analysis data to create comprehensive documentation suitable for both written analysis and visual diagram generation. Validate file references with getFileStructure and readSourceCode for accuracy.

Required outputs:
1. Synthesized insights from CFG and source analysis in one paragraph explaining the main flow suitable for diagram explanation
2. Critical interaction pathways that translate to clear diagram arrows and documentation flow
3. Final components (max 8, optimally 5) following {project_type} patterns with distinct boundaries for visual representation. Each should have a source file references, which are the main files/functions/classes/modules that represent the component
4. Component relationships (max 2 per component pair) that create clear diagram connections and define logical flow
5. Architecture overview paragraph suitable for both documentation and diagram generation prompts

Additional considerations for diagram generation:
- Components should have clear functional boundaries for flow graph representation
- Use clear naming conventions for components to enhance clarity in the context of the project itself.
- Relationships should represent clear data/control flow for arrow representation
- Include file/directory references suitable for interactive click events
- Group related functionality for potential diagram subgraphs

Constraints:
- Use only provided analysis data
- Focus on highest level architectural components suitable for both documentation and flow graph representation
- Exclude utility/logging components that clutter both documentation and diagrams
- Include description paragraph that works for project overview and diagram context"""

FEEDBACK_MESSAGE = """You are a software architect receiving expert feedback on your analysis for documentation and diagram optimization.

Feedback:
{feedback}

Original Analysis:
{analysis}

Instructions:
1. Evaluate feedback relevance to both analysis quality and diagram generation suitability
2. Use tools to address missing information or misinformation affecting the flow graph representation
3. Address only specific feedback points if they improve both documentation and diagram clarity

Required outputs:
1. Synthesized insights from CFG and source analysis explaining main flow for documentation and diagram context
2. Critical interaction pathways suitable for both written documentation and visual arrows
3. Keep the same final components (max 8, optimally 5) without changes unless explicitly requested for diagram improvement
4. Component relationships (max 2 per component pair) optimized for both documentation and flow graph representation
5. Architecture overview paragraph suitable for documentation and diagram generation

Constraints:
- Use only provided analysis data enhanced by feedback
- Focus on highest level architectural components suitable for the flow graph representation
- Exclude utility/logging components that complicate both documentation and diagrams
- Maintain consistency between documentation and diagram generation needs"""

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

SUBCFG_DETAILS_MESSAGE = """Analyze subgraph for component the following component in `{project_name}`:
Component: {component}

Control-flow Project Context:
{cfg_str}

Instructions:
No tools required - extract from provided CFG data only.

Output:
Return only the relevant subgraph for the specified component."""

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
- Component interactions (max 10 components, 2 relationships per pair)
- Justification based on {project_type} patterns

Focus on core subsystem functionality only."""

ENHANCE_STRUCTURE_MESSAGE = """Validate component analysis for {component} in `{project_name}`.

Project Context:
{meta_context}

Current insights:
{insight_so_far}

Instructions:
1. Review existing insights first
2. Use getPackageDependencies only if package relationships are unclear

Required outputs:
- Validated component abstractions from existing insights
- Refinements based on {project_type} patterns
- Confirmed component source files and relationships

Work primarily with provided insights."""

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
4. Component interactions (max 2 relationships per component pair)

Justify component choices based on fundamental architectural importance."""

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
2. Verify max 2 relationships per component pair
3. Assess relationship logical consistency

Output:
Conclude with VALID or INVALID assessment and specific reasoning."""

SYSTEM_DIFF_ANALYSIS_MESSAGE = """You are a software architecture expert analyzing code differences.

Instructions:
1. Analyze provided diff data first
2. Use tools if diff impact on architecture is unclear

Required outputs:
- Significant architectural changes from diff
- Impact assessment on existing architecture analysis
- Determination if architecture update is warranted"""

DIFF_ANALYSIS_MESSAGE = """Analyze architectural impact of code changes.

Original Analysis:
{analysis}

Code Changes:
{diff_data}

Instructions:
1. Review changes against existing architecture
2. Assess architectural significance
3. Provide impact score (0-10) with reasoning

Scoring guide:
- 0-2: Minor changes (variable/method renames)
- 3-4: Small changes (new methods, logic updates)
- 5-6: Medium changes (new classes, class logic changes)
- 7-8: Large changes (new modules, flow changes)
- 9-10: Major changes (architecture changes, major removals)

No tools required - use provided diff and analysis data only."""

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


class GeminiFlashBidirectionalPromptFactory(AbstractPromptFactory):
    """Concrete prompt factory for Gemini Flash bidirectional prompts."""

    def get_system_message(self) -> str:
        return SYSTEM_MESSAGE

    def get_cfg_message(self) -> str:
        return CFG_MESSAGE

    def get_source_message(self) -> str:
        return SOURCE_MESSAGE

    def get_classification_message(self) -> str:
        return CLASSIFICATION_MESSAGE

    def get_conclusive_analysis_message(self) -> str:
        return CONCLUSIVE_ANALYSIS_MESSAGE

    def get_feedback_message(self) -> str:
        return FEEDBACK_MESSAGE

    def get_system_details_message(self) -> str:
        return SYSTEM_DETAILS_MESSAGE

    def get_subcfg_details_message(self) -> str:
        return SUBCFG_DETAILS_MESSAGE

    def get_cfg_details_message(self) -> str:
        return CFG_DETAILS_MESSAGE

    def get_enhance_structure_message(self) -> str:
        return ENHANCE_STRUCTURE_MESSAGE

    def get_details_message(self) -> str:
        return DETAILS_MESSAGE

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

    def get_system_diff_analysis_message(self) -> str:
        return SYSTEM_DIFF_ANALYSIS_MESSAGE

    def get_diff_analysis_message(self) -> str:
        return DIFF_ANALYSIS_MESSAGE

    def get_system_meta_analysis_message(self) -> str:
        return SYSTEM_META_ANALYSIS_MESSAGE

    def get_meta_information_prompt(self) -> str:
        return META_INFORMATION_PROMPT

    def get_file_classification_message(self) -> str:
        return FILE_CLASSIFICATION_MESSAGE
