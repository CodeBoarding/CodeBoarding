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

CFG_MESSAGE = """Analyze Control Flow Graph for {project_name} with comprehensive diagram generation optimization.

<context>
{meta_context}
The Control-Flow data is represented in the following format, firstly we have clustered methods, which are closely related to each other and then we have the edges between them.
As not all methods are clustered, some methods are not part of any cluster. These methods are represented as single nodes and are also added to the graph and are listed below the clusters.
Control-flow Data:
{cfg_str}

The goal is to create a clear architectural overview that enables new team members to understand the system's flow and structure within their first week.
</context>

<instructions>
1. Analyze the provided CFG data first, identifying clear component boundaries for flow graph representation
2. You MUST use getPackageDependencies to get information on package structure for proper diagram organization
3. You MUST use getClassHierarchy when component relationships need clarification for accurate arrow connections
4. Apply {project_type} architectural patterns throughout your analysis
5. Focus on core business logic with clear data flow - deliberately exclude logging/error handling components that clutter diagrams
</instructions>

<thinking>
I need to extract:
1. Core modules (max 15) with interaction patterns suitable for flow graph representation
2. Abstract components with names, descriptions, and responsibilities
3. Component relationships (max 2 per pair) for diagram arrows and documentation flow
4. Source file references for interactive diagram elements
</thinking>"""

SOURCE_MESSAGE = """Validate and enhance component analysis for comprehensive documentation and diagram generation.

<context>
{meta_context}
Current analysis: {insight_so_far}

The goal is to refine the analysis to create documentation that helps new engineers navigate and understand the codebase effectively, with interactive diagrams for exploration.
</context>

<instructions>
1. Review current analysis to identify gaps and optimize for flow graph representation
2. If component structure needs clarification for diagram mapping, you MUST use getClassHierarchy
3. To ensure components have clear source file references for click events, you MUST use getSourceCode
4. If component boundaries need validation, you MUST use readFile for full file references
5. For directory/package references that create better high-level diagram components, you MUST use getFileStructure
6. Work primarily with existing insights, but use tools for missing information
</instructions>

<thinking>
I need to refine to:
1. 5-10 components with distinct responsibilities following {project_type} patterns
2. Clear component boundaries optimized for both analysis and visual representation
3. Verified source file references for interactive diagram elements
4. Relationships suitable for clear diagram connections and documentation flow

Consider both documentation clarity and flow graph representation.
</thinking>"""

CLASSIFICATION_MESSAGE = """Classify files into architectural components for {project_name}.

<context>
Components: {components}
Files: {files}

The goal is to accurately organize files into components to help new developers understand the codebase structure and navigate it effectively.
</context>

<instructions>
1. Match files to components based on functionality and architectural purpose
2. If a file's purpose is ambiguous or unclear, you MUST use the readFile tool to examine its contents before making a classification
3. If you cannot find a relevant component for a file after examination, classify it as part of the "Unclassified" component
4. Provide brief justification for each classification decision
</instructions>

<thinking>
I need to ensure each file is properly categorized to maintain clear architectural boundaries that will be reflected in the documentation and diagrams.
</thinking>"""

CONCLUSIVE_ANALYSIS_MESSAGE = """Create final architectural analysis for {project_name} optimized for comprehensive documentation and diagram generation.

<context>
{meta_context}
CFG insights: {cfg_insight}
Source insights: {source_insight}

The goal is to synthesize all analysis into documentation that enables new engineers to understand and navigate the system within their first week, supported by clear interactive diagrams.
</context>

<instructions>
1. Use provided analysis data to create comprehensive documentation suitable for both written analysis and visual diagram generation
2. If file references need validation for accuracy, you MUST use getFileStructure
3. For source code verification, you MUST use readSourceCode when accuracy is critical
4. Focus on highest level architectural components suitable for flow graph representation
5. Exclude utility/logging components that clutter both documentation and diagrams
</instructions>

<thinking>
I need to provide:
1. Main flow overview (one paragraph) explaining data flow suitable for documentation and diagram context
2. 5-8 components following {project_type} patterns with source references for interactive elements
3. Component relationships (max 2 per pair) for clear diagram arrows and comprehensive documentation flow
4. Architecture summary suitable for documentation and visual diagram generation

Additional considerations:
- Components should have distinct functional boundaries for flow graph representation
- Use clear naming conventions for diagram and documentation clarity
- Include file/directory references for interactive click events
- Group related functionality for potential diagram subgraphs
</thinking>"""

FEEDBACK_MESSAGE = """Improve analysis based on validation feedback for documentation and comprehensive diagram optimization.

<context>
Original: {analysis}
Feedback: {feedback}

The goal is to address feedback systematically while maintaining analysis integrity, ensuring the documentation helps new engineers understand the system quickly.
</context>

<instructions>
1. Evaluate feedback relevance to both analysis quality and diagram generation suitability
2. If missing information needs to be addressed, you MUST use appropriate tools (readFile, getClassHierarchy, getSourceCode, getFileStructure)
3. Focus only on changes that improve documentation clarity and comprehensive diagram generation suitability
4. Address feedback systematically while maintaining analysis integrity
</instructions>

<thinking>
I need to use tools to address missing information or clarify component relationships affecting both documentation and flow graph representation.
</thinking>"""

SYSTEM_DETAILS_MESSAGE = """You are analyzing a software component's internal structure.

Document subcomponents, relationships, and interfaces. Focus on architectural insights relevant to developers."""

SUBCFG_DETAILS_MESSAGE = """Analyze component structure: {component}

<context>
Data: {cfg_str}
Context: {project_name}

The goal is to understand the internal structure and execution flow to help developers navigate this specific component.
</context>

<instructions>
1. Extract information from the provided CFG data first
2. If interaction details or patterns are unclear, you may use getClassHierarchy
3. Map internal structure, execution flow, integration points, and design patterns
4. Focus on architectural significance rather than implementation details
</instructions>"""

CFG_DETAILS_MESSAGE = """Analyze component CFG: {component}

<context>
CFG data: {cfg_str}
Context: {meta_context}

The goal is to document control flow, dependencies, and interfaces for architectural understanding that helps developers work with this component.
</context>

<instructions>
1. Analyze provided CFG data for subsystem patterns first
2. If interaction details are unclear, you MUST use getClassHierarchy
3. Document control flow, dependencies, and interfaces for architectural understanding
4. Focus on core subsystem functionality only
</instructions>"""

ENHANCE_STRUCTURE_MESSAGE = """Enhance component analysis: {component}

<context>
Structure: {insight_so_far}
Context: {meta_context}

The goal is to validate and improve the component analysis for better developer understanding.
</context>

<instructions>
1. Review existing insights first to understand current analysis
2. If package relationships are unclear, you MUST use getPackageDependencies
3. Validate organization, identify gaps, and improve documentation
4. Focus on architectural patterns from the {project_type} context
5. Work primarily with provided insights
</instructions>"""

DETAILS_MESSAGE = """Provide component analysis: {component}

<context>
Context: {meta_context}

The goal is to create comprehensive component documentation that helps developers understand its role, capabilities, and how to work with it effectively.
</context>

<instructions>
1. Use provided analysis summary and context
2. Document internal organization, capabilities, interfaces, and development insights
3. Use {project_type} patterns as reference for architectural decisions
4. Focus on information that helps developers understand and modify this component
</instructions>"""

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
- Maximum 2 relationships per component pair
- Logical consistency of interactions
- Appropriate relationship descriptions

Output:
Conclude with VALID or INVALID assessment and specific reasoning."""

SYSTEM_DIFF_ANALYSIS_MESSAGE = """You analyze code changes for architectural impact.

<instructions>
1. Analyze provided diff data first to understand the scope of changes
2. If diff impact on architecture is unclear, you MUST use appropriate tools (readFile, getClassHierarchy)
3. Classify changes by type and assess architectural significance
4. Provide impact scores from 0-10 (cosmetic to architectural)
5. Focus on component boundaries and relationships over code volume
</instructions>

<thinking>
Required outputs:
- Significant architectural changes from diff
- Impact assessment on existing architecture analysis
- Determination if architecture update is warranted
</thinking>"""

DIFF_ANALYSIS_MESSAGE = """Assess code change impact:

<context>
Analysis: {analysis}
Changes: {diff_data}

The goal is to understand how code changes affect the architectural documentation and whether updates are needed.
</context>

<instructions>
1. Review changes against existing architecture first
2. Classify changes by type and architectural significance
3. Evaluate architectural significance using 0-10 scale with clear justification
4. Focus on component boundaries and relationships over code volume
5. Determine if architecture documentation needs updates
</instructions>"""

SYSTEM_META_ANALYSIS_MESSAGE = """You extract architectural metadata from projects.

<instructions>
1. Start by examining available project context and structure
2. You MUST use readFile to analyze project documentation when available
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
1. You MUST use readFile to examine project documentation (README, setup files) to understand purpose and domain
2. You MUST use getFileStructure to examine file structure and identify the technology stack
3. You MUST use getPackageDependencies to understand dependencies and frameworks used
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


class ClaudeBidirectionalPromptFactory(AbstractPromptFactory):
    """Optimized prompt factory for Claude bidirectional prompts."""

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
