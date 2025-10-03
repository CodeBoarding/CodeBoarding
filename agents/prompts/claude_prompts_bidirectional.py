from .abstract_prompt_factory import AbstractPromptFactory

# Highly optimized prompts for Claude performance
SYSTEM_MESSAGE = """You are a software architecture expert analyzing {project_name} with comprehensive diagram generation optimization.

Project context: {meta_context}

Create architectural documentation and visual diagrams by analyzing control flows and component relationships. Prioritize clarity, accuracy, diagram generation suitability, and interactive element integration.

Focus on:
- Components with distinct visual boundaries for flow graph representation
- Source file references for interactive diagram elements
- Clear data flow optimization excluding utility/logging components that clutter diagrams"""

CFG_MESSAGE = """Analyze Control Flow Graph for {project_name} with comprehensive diagram generation optimization.

Context: {meta_context}
CFG Data: {cfg_str}

Extract:
1. Core modules (max 15) with interaction patterns suitable for flow graph representation
2. Abstract components with names, descriptions, and responsibilities
3. Component relationships (max 2 per pair) for diagram arrows and documentation flow
4. Source file references for interactive diagram elements

Apply {project_type} patterns. Focus on core business logic with clear data flow - exclude logging/error handling components that clutter diagrams.

Use getPackageDependencies for package structure organization. Use getClassHierarchy when relationships need clarification for arrow connections."""

SOURCE_MESSAGE = """Validate and enhance component analysis for comprehensive documentation and diagram generation.

Context: {meta_context}
Current analysis: {insight_so_far}

Refine to:
1. 5-10 components with distinct responsibilities following {project_type} patterns
2. Clear component boundaries optimized for both analysis and visual representation
3. Verified source file references for interactive diagram elements
4. Relationships suitable for clear diagram connections and documentation flow

Use getClassHierarchy for component structure clarification. Use getSourceCode for source file references. Use readFile for boundary validation. Use getFileStructure for directory/package references.

Work primarily with existing insights. Consider both documentation clarity and flow graph representation."""

CLASSIFICATION_MESSAGE = """Classify files into architectural components.

Project: {project_name}
Components: {components}
Files: {files}

Match files to components based on functionality. Use readFile for unclear cases. Create "Unclassified" component for files that don't fit existing categories.

Justify each classification briefly."""

CONCLUSIVE_ANALYSIS_MESSAGE = """Create final architectural analysis for {project_name} optimized for comprehensive documentation and diagram generation.

Context: {meta_context}
CFG insights: {cfg_insight}
Source insights: {source_insight}

Provide:
1. Main flow overview (one paragraph) explaining data flow suitable for documentation and diagram context
2. 5-8 components following {project_type} patterns with source references for interactive elements
3. Component relationships (max 2 per pair) for clear diagram arrows and comprehensive documentation flow
4. Architecture summary suitable for documentation and visual diagram generation

Additional considerations:
- Components should have distinct functional boundaries for flow graph representation
- Use clear naming conventions for diagram and documentation clarity
- Include file/directory references for interactive click events
- Group related functionality for potential diagram subgraphs

Exclude utility/logging components that clutter both documentation and diagrams."""

FEEDBACK_MESSAGE = """Improve analysis based on validation feedback for documentation and comprehensive diagram optimization.

Original: {analysis}
Feedback: {feedback}
Context: {meta_context}

Address feedback systematically while maintaining analysis integrity. Focus only on changes that improve documentation clarity and comprehensive diagram generation suitability.

Use tools to address missing information or clarify component relationships affecting both documentation and flow graph representation."""

SYSTEM_DETAILS_MESSAGE = """You are analyzing a software component's internal structure.

Document subcomponents, relationships, and interfaces. Focus on architectural insights relevant to developers."""

SUBCFG_DETAILS_MESSAGE = """Analyze component structure: {component}

Data: {cfg_str}
Context: {project_name}

Map internal structure, execution flow, integration points, and design patterns. Focus on architectural significance."""

CFG_DETAILS_MESSAGE = """Analyze component CFG: {component}

CFG data: {cfg_str}
Context: {meta_context}

Document control flow, dependencies, and interfaces for architectural understanding."""

ENHANCE_STRUCTURE_MESSAGE = """Enhance component analysis: {component}

Structure: {insight_so_far}
Context: {meta_context}

Validate organization, identify gaps, and improve documentation focusing on architectural patterns."""

DETAILS_MESSAGE = """Provide component analysis: {component}

Context: {meta_context}

Document internal organization, capabilities, interfaces, and development insights. Use {project_type} patterns as reference."""

PLANNER_SYSTEM_MESSAGE = """You evaluate components for detailed analysis based on complexity and significance.

Focus on architectural impact rather than implementation details."""

EXPANSION_PROMPT = """Evaluate expansion necessity: {component}

Determine if this component represents a complex subsystem warranting detailed analysis.

Simple components (few classes/functions): NO expansion
Complex subsystems (multiple interacting modules): CONSIDER expansion

Provide clear reasoning based on architectural complexity."""

VALIDATOR_SYSTEM_MESSAGE = """You validate architectural analysis quality.

Assess component clarity, relationship accuracy, source references, and overall coherence."""

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

Classify changes, assess risks, and score impact from 0-10 (cosmetic to architectural)."""

DIFF_ANALYSIS_MESSAGE = """Assess code change impact:

Analysis: {analysis}
Changes: {diff_data}

Classify changes, evaluate architectural significance (0-10 scale), and provide clear justification.

Focus on component boundaries and relationships over code volume."""

SYSTEM_META_ANALYSIS_MESSAGE = """You extract architectural metadata from projects.

Identify project type, domain, technology stack, and component patterns to guide analysis."""

META_INFORMATION_PROMPT = """Analyze project '{project_name}' to extract architectural metadata for comprehensive analysis optimization.

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

Focus on insights that guide component identification, flow visualization, and documentation generation for comprehensive clarity."""

FILE_CLASSIFICATION_MESSAGE = """Find which file contains: `{qname}`

Files: {files}

Select exactly one file path. Use readFile to locate definitions.
Include line numbers if identifying a specific function, method, or class."""


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