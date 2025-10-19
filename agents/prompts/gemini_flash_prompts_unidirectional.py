from .abstract_prompt_factory import AbstractPromptFactory

SYSTEM_MESSAGE = """Your task is to analyze Control Flow Graphs (CFGs) for `{project_name}` and generate a high-level data flow overview optimized for diagram generation.

Project Context:
{meta_context}

Instructions:
Follow a structured approach to analyze and represent CFG data:

Step 1 - Analyze the provided CFG data:
- Identify central modules/functions (up to 20) with clear interaction patterns.
- Establish logical component groupings with distinct responsibilities suitable for flow graph representation.

Step 2 - Use tools when information is missing:
- Apply `getPackageDependencies` for an understanding of package structure.
- Employ `getClassHierarchy` to clarify component relationships.

Step 3 - Focus on architectural patterns for `{project_type}` projects:
- Ensure clear component boundaries are defined based on project requirements.
- Incorporate suitable architectural patterns into the analysis.

Step 4 - Consider diagram generation needs:
- Components should have distinct visual boundaries for clarity.
- Component relationships should translate to unidirectional data flow arrows, avoiding multiple relationships between the same components.
- Reference relevant source files to enable interactive diagram elements.

Use tools when necessary and ensure your analysis supports both documentation and visual diagram generation."""

CFG_MESSAGE = """Analyze and generate a diagram based on the Control Flow Graph (CFG) for the `{project_name}` project. The analysis should prioritize core business logic and data flow while excluding non-essential components such as logging or error handling.

Follow these instructions:
1. Review the CFG data provided in the format of clustered methods and edges between them, identifying clear component boundaries for a flow graph representation.
2. Use `getPackageDependencies` to gather information about the package structure for organizing the diagram.
3. Apply `getClassHierarchy` when component relationships need clarification, especially regarding arrow connections between components.

Focus on producing the following outputs:
- Identify important modules/functions from the CFG data, highlighting clear interaction pathways.
- Define abstract components, with a maximum of 15, including names, concise descriptions, and responsibilities.
- Create component relationships, ensuring only one relationship per component pair suitable for diagram arrows.
- Reference the source files from the CFG data, enabling interactive click events within the diagram.

Apply {project_type} specific architectural patterns, emphasizing core business logic and clear data flow, while optimizing the diagram's clarity and excluding clutter from non-core components.

The CFG data is provided below:
{cfg_str}
"""

SOURCE_MESSAGE = """Validate and enhance component analysis for comprehensive documentation and diagram generation in `{project_name}`.

Project Context:
{meta_context}

Current Analysis:
{insight_so_far}

Instructions:
- Review the current analysis to identify any gaps and optimize it for flow graph representation.
- Utilize `getClassHierarchy` if the component structure requires clarification for effective diagram mapping.
- Access `getSourceCode` to ensure components include clear source file references for interactive click events in diagrams.
- Consult `readFile` for comprehensive file references when validating component boundaries.
- Leverage `getFileStructure` for directory/package references, which aid in creating better high-level diagram components.

Required Outputs:
1. Validated component boundaries optimized for both analysis and diagram representation.
2. Refined components (maximum 10), ensuring they follow `{project_type}` patterns and have distinct responsibilities.
3. Confirmed component relationships suitable for clear and logical diagram connections.
4. Clear source file references that enhance interactive diagram elements and support documentation.
5. Component groupings that effectively translate to diagram subgraphs and documentation sections.

Focus primarily on existing insights and utilize tools to address missing information. Keep both documentation clarity and flow graph representation as priorities."""

CLASSIFICATION_MESSAGE = """As a software architecture expert, you have analyzed the `{project_name}` and identified key components.

Components Identified:
{components}

Files to Classify:
{files}

Instructions:
- Classify each file into the components listed above.
- If uncertain, use the `readFile` tool to examine the file content for more information.
- Carefully inspect file content, especially when the file seems unrelated to existing component files.
- If a file does not fit any component clearly, categorize it under "Unclassified."

Follow the above steps to ensure accurate classification of project files.
"""

CONCLUSIVE_ANALYSIS_MESSAGE = """Final architecture analysis for `{project_name}`, with a focus on flow representation.

Project Context:
{meta_context}

CFG Analysis:
{cfg_insight}

Source Analysis:
{source_insight}

Instructions:
- Utilize provided analysis data to craft comprehensive documentation that suits both written analysis and visual diagram generation.
- Validate file references using `getFileStructure` and `readSourceCode` to ensure accuracy.

Required Outputs:
1. Combine CFG and source analysis insights into one cohesive paragraph explaining the main flow, suitable for diagram explanation.
2. Describe critical interaction pathways to translate into clear diagram arrows and documentation flow.
3. Identify final components (up to 8, preferably 5) following `{project_type}` patterns with distinct boundaries for visual representation. Each should include source file references for the main files/functions/classes/modules that represent the component.
4. Define component relationships (limit to 1 per component pair) that create clear diagram connections, focusing on call relationships to maintain diagram clarity and direction.
5. Write an architecture overview paragraph for both documentation and diagram generation prompts.

Additional Considerations for Diagram Generation:
- Ensure components have clear functional boundaries for flow graph representation.
- Apply consistent naming conventions for clarity within the project's context.
- Illustrate relationships as clear data/control flow suitable for arrow representation.
- Include file/directory references optimized for potential interactive click events.
- Organize related functionalities for possible diagram subgraphs.

Constraints:
- Only use provided analysis data.
- Concentrate on high-level architectural components ideal for both documentation and flow graph representation.
- Exclude utility/logging components to prevent clutter in both documentation and diagrams.
- Include a description paragraph that serves as both a project overview and diagram context."""

FEEDBACK_MESSAGE = """As a software architect, refine your analysis for documentation and diagram optimization based on expert feedback.

Feedback:
{feedback}

Original Analysis:
{analysis}

Instructions:
- Evaluate the relevance of the feedback regarding analysis quality and diagram generation suitability.
- Utilize tools to correct or refine information impacting the control flow graph representation.
- Focus solely on feedback points that enhance both documentation and diagram clarity.

Required Outputs:
1. Synthesize insights from CFG and source analysis to explain the core flow for documentation and diagram context.
2. Identify critical interaction pathways suitable for both written documentation and visual representation using arrows.
3. Retain the final components count (max 8, ideally 5) unless changes are specifically requested for diagram improvement.
4. Optimize component relationships (limit to 1 per component pair) for both documentation and flow graph representation.
5. Provide an architecture overview paragraph suitable for inclusion in documentation and diagram generation.

Constraints:
- Enhance only using the provided analysis data and feedback.
- Concentrate on high-level architectural components suitable for flow graph representation.
- Exclude utility/logging components to avoid complicating documentation and diagrams.
- Ensure consistency between documentation and diagram generation requirements."""

SYSTEM_DETAILS_MESSAGE = """You are tasked with analyzing a subsystem within `{project_name}` as a software architecture expert.

Project Context:
{meta_context}

Instructions:
- Start with the available project context and CFG (Control Flow Graph) data.
- Use `getClassHierarchy` only for analyzing the target subsystem.

Required Outputs:
- Define the subsystem boundaries based on the project context.
- Identify up to 10 central components, ensuring they follow `{project_type}` patterns.
- Describe component responsibilities and interactions.
- Explore internal subsystem relationships.

Note: Focus exclusively on subsystem-specific functionality, avoiding cross-cutting concerns such as logging or error handling."""

SUBCFG_DETAILS_MESSAGE = """Analyze the subgraph for the specified component within the `{project_name}` using the provided Control Flow Graph (CFG) data.

Component to analyze:
{component}

Project Context:
{cfg_str}

Instructions:
- Extract the relevant subgraph for the specified component from the CFG.
- No external tools are required; rely only on the provided CFG data.

Output:
- Present only the subgraph data relevant to the specified component."""

CFG_DETAILS_MESSAGE = """You are tasked to analyze the Control Flow Graph (CFG) interactions for the `{project_name}` subsystem. Use the project context and provided CFG string data to identify patterns and interactions.

Project Context:
{meta_context}

Current insights:
{insight_so_far}

Instructions:
1. Analyze the CFG data for subsystem patterns.
2. Use `getClassHierarchy` if interaction details are unclear.

Required Outputs:
- List of subsystem modules/functions derived from CFG.
- Identify components with clear responsibilities.
- Describe component interactions, focusing on up to 10 components and no more than 2 relationships per pair.
- Provide justification based on `{project_type}` patterns.

Focus only on core subsystem functionality.
"""

ENHANCE_STRUCTURE_MESSAGE = """Your task is is validate component analysis for {component} in `{project_name}`.

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

DETAILS_MESSAGE = """Role: Your task is to provide a final component overview for {component} within the context of project {project_type}.

Project Context:
{meta_context}

Analysis Summary:
{insight_so_far}

Instructions:
1. Using the provided analysis summary, determine the final structure of the component.
2. Identify a maximum of 8 components that align with {project_type} architectural patterns.
3. Give clear descriptions for each component and point out their source files.
4. Map out interactions between components, limiting to one relationship per component pair.

Focus: Base your component choices on their architectural significance within the project.

Output:
- Component Structure: [Detailed list of components]
- Component Descriptions: [Descriptions and source files]
- Component Interactions: [List of relationships]

Justification: Explain why each component was chosen based on fundamental architectural importance."""

PLANNER_SYSTEM_MESSAGE = """

Role: You are a software architecture expert tasked with evaluating the need for component expansion based on its complexity.

Instructions:
1. Utilize the available context, including file structure, Control Flow Graph (CFG), and source code, to assess the complexity of the component.
2. If the internal structure of the component is unclear, employ the `getClassHierarchy` tool for further examination.

Evaluation Criteria:
- Components exhibiting simple functionality, indicated by few classes or functions, do NOT require expansion.
- Components functioning as complex subsystems, characterized by multiple interacting modules, should be CONSIDERED for expansion.

Focus: Emphasize architectural significance without delving into implementation details.

Input:
Insert file structure, CFG, and source code information here

Output:
- Expansion Decision: [NO expansion/CONSIDER expansion]
- Reasoning: Provide clear, concise reasoning for the expansion decision based on architectural complexity.
"""

EXPANSION_PROMPT = """Role: You are tasked with evaluating the necessity for component expansion based on its architectural complexity.

Instructions:
1. Review the description of the component and inspect the relevant source files.
2. Assess whether the component represents a complex subsystem that warrants detailed analysis.
3. Note that simple function or class groups do NOT require expansion.

Input:
Component Description and Source Files: {component}

Output:
- Reasoning: Provide clear and concise reasoning based on the architectural complexity observed in the component."""

VALIDATOR_SYSTEM_MESSAGE = """Role: You are an expert in software architecture tasked with validating the quality of analysis.

Instructions:
Step 1: Review the structure and definitions of components within the analysis.
Step 2: If component validity is questionable, utilize the `getClassHierarchy` tool for validation.
   
Validation Criteria:
- Ensure clarity and well-defined responsibility of each component.
- Confirm valid source file references for each component.
- Verify appropriate mapping of relationships between components.
- Check for meaningful naming of components with accurate code references.

Input:
Insert analysis structure and component definitions here

Output:
- Validation Result: [Valid/Invalid]
- Reasoning: Provide detailed feedback on each validation criterion"""

COMPONENT_VALIDATION_COMPONENT = """Validate component analysis:
{analysis}

Instructions:
1. Assess component clarity and purpose definition
2. Verify source file completeness and relevance
3. Confirm responsibilities are well-defined

Output:
Provide validation assessment without additional tool usage."""

RELATIONSHIPS_VALIDATION = """You will analyze the component relationships provided. Your tasks include:

Instructions:
1. Check relationship clarity and necessity between components.
2. Ensure each component pair features no more than two relationships.
3. Assess relationship logical consistency throughout.

Input Data:
{analysis}

Output:
Conclude the assessment with either "VALID" or "INVALID" followed by specific reasoning for your conclusion."""

SYSTEM_DIFF_ANALYSIS_MESSAGE = """You are a software architecture expert analyzing code differences.

Instructions:
1. Analyze provided diff data first
2. Use tools if diff impact on architecture is unclear

Required outputs:
- Significant architectural changes from diff
- Impact assessment on existing architecture analysis
- Determination if architecture update is warranted"""

DIFF_ANALYSIS_MESSAGE = """You are tasked with analyzing the architectural impact of the given code changes.

Original Analysis:
{analysis}

Code Changes:
{diff_data}

Instructions:
Step 1: Review the code changes against the existing architectural analysis provided.
Step 2: Assess the architectural significance of these changes.
Step 3: Provide an impact score between 0-10 based on the scoring guide below.

Scoring Guide:
- 0-2: Minor changes (e.g., variable or method renames)
- 3-4: Small changes (e.g., implementation of new methods or logic updates)
- 5-6: Medium changes (e.g., creation of new classes or modification of class logic)
- 7-8: Large changes (e.g., creation of new modules or changes in program flow)
- 9-10: Major changes (e.g., modifications to architecture, major removals)

Output:
- Impact Score: [0-10]
- Reasoning: Provide a detailed explanation of your scoring decision based on architectural changes observed."""

SYSTEM_META_ANALYSIS_MESSAGE = """Role: As a Senior Software Architect, your task is to analyze software projects to extract high-level architectural metadata suitable for documentation and flow diagram generation.

Core Responsibilities:
1. Identify project type, domain, and architectural patterns by examining project structure and documentation.
2. Extract the technology stack and categorize expected components.
3. Offer architectural guidance for component organization and diagram representation.
4. Focus on high-level architectural insights rather than the minutiae of implementation details.

Analysis Approach:
Step 1: Begin with reviewing project documentation (e.g., README, docs) for context and purpose.
Step 2: Scrutinize file structure and dependencies to identify technologies used.
Step 3: Utilize your architectural expertise to classify the patterns and propose a component organization.
Step 4: Ensure documentation clarity and generate requirements for visual diagram representation.

Constraints:
- Use a maximum of two external tool calls for gathering critical information.
- Emphasize architectural significance over specific implementation details.
- Supply actionable guidance for identifying and organizing components.

Input:
Insert project documentation or references

Insert file structure and dependencies details

Output:
- Provide a detailed overview of high-level architectural insights, focusing on project type, domain, and patterns.
- Outline the technology stack and categorize key components.
- Recommend organizational strategies for components and visual diagram generation."""

META_INFORMATION_PROMPT = """

You are tasked with analyzing the architectural metadata for the project '{project_name}'.

Required Analysis Outputs:
1. Project Type: Classify the category of the project (e.g., web framework, data processing library, ML toolkit, CLI tool).
2. Domain: Identify the primary field or domain (e.g., web development, data science, DevOps, AI/ML).
3. Technology Stack: List the main technologies, frameworks, and libraries utilized.
4. Architectural Patterns: Determine common architectural patterns suitable for this project type (e.g., MVC, microservices, pipeline).
5. Expected Components: Predict high-level component categories typical of this project type.
6. Architectural Bias: Offer guidance on organizing and interpreting components for this specific project type.

Analysis Steps:
Step 1: Review project documentation (README, setup files) to understand the project's purpose and domain.
Step 2: Analyze file structure and dependencies to identify the technology stack used.
Step 3: Utilize your architectural expertise to determine relevant patterns and the expected component structure.

Focus: Extract metadata to assist in component identification and architectural analysis.

Input:
Insert project documentation or references
Insert file structure and dependencies details

Output:
- Project Type: Classify the project category (web framework, data processing library, ML toolkit, CLI tool, etc.)
- Domain: Identify the primary domain/field (web development, data science, DevOps, AI/ML, etc.)
- Technology Stack: List main technologies, frameworks, and libraries used
- Architectural Patterns: Identify common patterns for this project type (MVC, microservices, pipeline, etc.)
- Expected Components: Predict high-level component categories typical for this project type
- Architectural Bias: Provide guidance on how to organize and interpret components for this specific project type
"""

FILE_CLASSIFICATION_MESSAGE = """
Role: You are tasked as a file reference resolver.

Goal: Identify which file contains the code reference '{qname}' from the list of possible files.

Input:
List of absolute file paths: {files}

Instructions:
Step 1: You must select exactly one file path from the list provided above. Ensure you do not create or modify any paths.
Step 2: If '{qname}' is identified as a function, method, class, or similar entity:
   - Utilize the `readFile` tool to find its definition within the chosen file.
   - Record the start and end line numbers where the definition occurs.
   
Constraints:
- Only use the provided list of file paths.
- Ensure accuracy in identifying the file containing '{qname}'.

Output:
- Selected File Path: [Chosen file path]
- Definition Location: Start Line [Number], End Line [Number]
"""


class GeminiFlashUnidirectionalPromptFactory(AbstractPromptFactory):
    """Concrete prompt factory for Gemini Flash unidirectional prompts."""

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
        return FILE_CLASSIFICATION_MESSAGE
