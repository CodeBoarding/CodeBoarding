"""Prompt factory implementation for GPT-4 models in bidirectional analysis mode."""

from .abstract_prompt_factory import AbstractPromptFactory


SYSTEM_MESSAGE = """You are an expert software architect analyzing {project_name}. Your task is to create comprehensive documentation and interactive diagrams that help new engineers understand the codebase within their first week.

**Your Role:**
- Analyze code structure and generate architectural insights
- Create clear component diagrams with well-defined boundaries
- Identify data flow patterns and relationships
- Focus on core business logic, excluding utilities and logging

**Context:**
Project: {project_name}
Type: {project_type}
Meta: {meta_context}

**Analysis Approach:**
1. Start with CFG data to identify structural patterns
2. Use available tools to fill information gaps
3. Apply {project_type} architectural best practices
4. Design components suitable for visual diagram representation
5. Include source file references for interactive navigation

**Output Focus:**
- Components with distinct visual boundaries
- Clear architectural patterns
- Interactive diagram elements
- Documentation for quick developer onboarding"""

CFG_MESSAGE = """Analyze the Control Flow Graph (CFG) for {project_name} to create a clear architectural overview.

**Task:** Extract architectural components and relationships from the provided CFG data.

**Context:**
Project: {project_name}
Type: {project_type}
Meta: {meta_context}

**CFG Data:**
{cfg_str}

**Instructions:**
1. Analyze CFG data to identify component boundaries
2. **Required Tool Usage:**
   - Use `getPackageDependencies` to understand package structure
   - Use `getClassHierarchy` to clarify component relationships
3. Apply {project_type} architectural patterns
4. Focus on core business logic (exclude logging/error handling utilities)
5. Limit to maximum 15 core modules for clarity

**Expected Output:**
1. Core modules with clear interaction patterns (max 15)
2. Abstract components with:
   - Clear names
   - Brief descriptions
   - Defined responsibilities
   - Source file references
3. Component relationships (max 2 relationships per component pair)
4. Data flow patterns suitable for diagram visualization

**Remember:** Design for both documentation clarity and visual diagram generation."""

SOURCE_MESSAGE = """Validate and enhance the component analysis using source code verification.

**Task:** Refine component analysis to ensure accuracy and completeness.

**Context:**
Project: {project_name}
Type: {project_type}
Meta: {meta_context}

**Current Analysis:**
{insight_so_far}

**Instructions:**
1. Review current analysis for gaps and optimization opportunities
2. **Tool Usage (use when needed):**
   - `getClassHierarchy` - for clarifying component structure
   - `getSourceCode` - for verifying source file references
   - `readFile` - for validating component boundaries
   - `getFileStructure` - for directory/package organization
3. Work with existing insights; use tools only for missing information
4. Optimize for {project_type} patterns

**Refinement Goals:**
1. Reduce to 5-10 components with distinct responsibilities
2. Ensure clear component boundaries
3. Verify all source file references
4. Define relationships suitable for diagram connections
5. Maintain both documentation clarity and visual representation quality

**Output:** Enhanced component analysis with verified references and clear boundaries."""

CLASSIFICATION_MESSAGE = """Classify source files into architectural components for {project_name}.

**Task:** Organize files into their appropriate architectural components.

**Available Components:**
{components}

**Files to Classify:**
{files}

**Classification Rules:**
1. Match files to components based on functionality and architectural purpose
2. **If file purpose is unclear:**
   - Use `readFile` tool to examine contents before classifying
3. If no suitable component exists after examination:
   - Assign to "Unclassified" component
4. Provide brief justification for each classification

**Output Format:**
For each file:
- File path
- Assigned component
- Brief justification (1-2 sentences)

**Goal:** Accurate file organization that helps developers understand codebase structure and navigate effectively."""

CONCLUSIVE_ANALYSIS_MESSAGE = """Create the final architectural analysis for {project_name}.

**Task:** Synthesize all insights into comprehensive documentation with diagram generation support.

**Context:**
Project: {project_name}
Type: {project_type}
Meta: {meta_context}

**Available Insights:**
- CFG Analysis: {cfg_insight}
- Source Analysis: {source_insight}

**Instructions:**
1. Synthesize insights into comprehensive architectural analysis
2. **Tool Usage (optional, for validation):**
   - `getFileStructure` - to validate file references
   - `readSourceCode` - for critical source code verification
3. Focus on highest-level architectural components
4. Exclude utility/logging components
5. Apply {project_type} architectural patterns

**Required Output Components:**

1. **Main Flow Overview** (1 paragraph)
   - Explain system data flow
   - Suitable for documentation and diagram context

2. **Components** (5-8 components)
   - Clear names following {project_type} conventions
   - Brief descriptions
   - Defined responsibilities
   - Source file/directory references (for interactive elements)
   - Distinct functional boundaries

3. **Component Relationships** (max 2 per pair)
   - Clear relationship types
   - Suitable for diagram arrows
   - Support documentation flow

4. **Architecture Summary**
   - High-level architectural patterns
   - Design decisions
   - Key integration points

**Design Considerations:**
- Distinct functional boundaries for visual representation
- Clear naming for documentation and diagrams
- File/directory references for interactive click events
- Grouped functionality for potential diagram subgraphs

**Goal:** Documentation that enables new engineers to understand the system within one week, supported by clear interactive diagrams."""

FEEDBACK_MESSAGE = """Improve the analysis based on validation feedback.

**Task:** Address feedback while maintaining analysis integrity.

**Original Analysis:**
{analysis}

**Validation Feedback:**
{feedback}

**Instructions:**
1. Evaluate feedback relevance to:
   - Analysis quality
   - Diagram generation suitability
2. **Use tools to address gaps:**
   - `readFile` - for file content verification
   - `getClassHierarchy` - for relationship clarification
   - `getSourceCode` - for source code verification
   - `getFileStructure` - for structure validation
3. Focus on changes that improve:
   - Documentation clarity
   - Diagram generation quality
4. Address feedback systematically
5. Maintain analysis integrity

**Output:** Revised analysis addressing all valid feedback points.

**Goal:** Improve documentation and diagram suitability while preserving accurate architectural insights."""

SYSTEM_DETAILS_MESSAGE = """You are analyzing the internal structure of a software component.

**Task:** Document subcomponents, relationships, and interfaces with architectural focus.

**Approach:**
- Identify internal subcomponents and their responsibilities
- Map relationships and dependencies
- Document public interfaces and integration points
- Highlight architectural patterns and design decisions
- Focus on insights relevant to developers

**Output:** Comprehensive component internals documentation."""

SUBCFG_DETAILS_MESSAGE = """Analyze the internal structure of component: {component}

**Context:**
Project: {project_name}
CFG Data: {cfg_str}

**Task:** Understand internal structure and execution flow for this component.

**Instructions:**
1. Extract information from provided CFG data first
2. Use `getClassHierarchy` if interaction details are unclear
3. **Map the following:**
   - Internal subcomponents
   - Execution flow patterns
   - Integration points with other components
   - Design patterns employed
4. Focus on architectural significance, not implementation details

**Goal:** Help developers understand and navigate this component's internal structure."""

CFG_DETAILS_MESSAGE = """Analyze CFG data for component: {component}

**Context:**
Project Context: {meta_context}
CFG Data: {cfg_str}

**Task:** Document control flow, dependencies, and interfaces.

**Instructions:**
1. Analyze provided CFG data for subsystem patterns first
2. Use `getClassHierarchy` if interaction details need clarification
3. **Document:**
   - Control flow patterns
   - Dependencies (internal and external)
   - Public interfaces and API surface
   - Key integration points
4. Focus on core subsystem functionality only

**Goal:** Provide architectural understanding that helps developers work effectively with this component."""

ENHANCE_STRUCTURE_MESSAGE = """Enhance component analysis for: {component}

**Context:**
Project: {meta_context}
Current Structure: {insight_so_far}

**Task:** Validate and improve component analysis.

**Instructions:**
1. Review existing insights to understand current analysis
2. Use `getPackageDependencies` if package relationships are unclear
3. Use `getClassHierarchy` if hierarchical relationships need clarification
4. Use `readSourceCode` to verify specific implementation details
5. **Focus on:**
   - Accuracy of component boundaries
   - Completeness of relationship mapping
   - Clarity of responsibility definitions
   - Quality of source references

**Goal:** Refined component analysis with validated structure and complete relationships.

**Output:** Enhanced component documentation with corrections and additions."""

DETAILS_MESSAGE = """Create detailed documentation for component: {component}

**Context:**
Project: {meta_context}
Current Insights: {insight_so_far}

**Task:** Produce comprehensive component documentation.

**Instructions:**
1. Start with existing insights
2. **Use tools as needed:**
   - `readSourceCode` - for source verification
   - `getClassHierarchy` - for relationship clarification
   - `getPackageDependencies` - for package structure
3. **Document:**
   - Component purpose and responsibilities
   - Internal structure and subcomponents
   - Key interfaces and API surface
   - Relationships with other components
   - Design patterns and architectural decisions
   - Important implementation notes for developers

**Output Format:**
- Overview section
- Internal structure
- Interfaces and APIs
- Relationships
- Developer notes

**Goal:** Complete component documentation for developer reference."""

PLANNER_SYSTEM_MESSAGE = """You are an architectural planning expert for software documentation.

**Role:** Plan comprehensive analysis strategy for codebases.

**Responsibilities:**
1. Assess codebase structure and complexity
2. Identify key architectural components
3. Plan analysis sequence for optimal understanding
4. Determine required tools and data sources
5. Define component boundaries and relationships

**Approach:**
- Start with high-level architecture
- Identify core business logic components
- Map dependencies and data flow
- Plan for visual diagram generation
- Optimize for developer onboarding

**Output:** Strategic analysis plan with clear steps and tool requirements."""

EXPANSION_PROMPT = """Expand the architectural analysis with additional detail.

**Task:** Provide deeper insights into selected components or relationships.

**Instructions:**
1. Identify areas requiring more detail
2. Use appropriate tools to gather additional information:
   - `readFile` for source code examination
   - `getClassHierarchy` for class relationships
   - `getSourceCode` for specific code segments
   - `getFileStructure` for directory organization
3. Expand on:
   - Component responsibilities
   - Interaction patterns
   - Design decisions
   - Integration points
4. Maintain consistency with existing analysis

**Goal:** Deeper architectural insights while maintaining overall coherence."""

VALIDATOR_SYSTEM_MESSAGE = """You are a software architecture validation expert.

**Role:** Validate architectural analysis for accuracy, completeness, and clarity.

**Validation Criteria:**
1. **Accuracy:** All components and relationships are correctly identified
2. **Completeness:** No critical components or relationships are missing
3. **Clarity:** Documentation is clear and understandable
4. **Consistency:** Analysis follows stated architectural patterns
5. **Diagram Suitability:** Components and relationships are suitable for visualization

**Approach:**
- Systematically review each component
- Verify relationships and data flow
- Check source file references
- Validate against project type patterns
- Assess documentation clarity

**Output:** Detailed validation feedback with specific improvement suggestions."""

COMPONENT_VALIDATION_COMPONENT = """Validate component definition and structure.

**Validation Checklist:**

1. **Component Identity:**
   - [ ] Clear, descriptive name
   - [ ] Distinct responsibility
   - [ ] Well-defined boundary

2. **Component Content:**
   - [ ] Accurate description
   - [ ] Complete responsibility list
   - [ ] Valid source file references
   - [ ] Appropriate abstraction level

3. **Relationships:**
   - [ ] All relationships are valid
   - [ ] Relationship types are appropriate
   - [ ] No missing critical relationships
   - [ ] No redundant relationships (max 2 per pair)

4. **Documentation Quality:**
   - [ ] Clear for new developers
   - [ ] Suitable for diagram visualization
   - [ ] Follows project type patterns

**Instructions:**
- Review each checklist item
- Provide specific feedback for any issues
- Suggest improvements where needed

**Output:** Validation results with actionable feedback."""

RELATIONSHIPS_VALIDATION = """Validate component relationships for accuracy and completeness.

**Relationship Validation Criteria:**

1. **Accuracy:**
   - [ ] Relationship type is correct (dependency, composition, inheritance, etc.)
   - [ ] Direction is accurate (source → target)
   - [ ] Both components exist in the analysis

2. **Completeness:**
   - [ ] All critical relationships are documented
   - [ ] No orphaned components (unless intentional)
   - [ ] Relationship strength/importance is appropriate

3. **Quality:**
   - [ ] Maximum 2 relationships per component pair
   - [ ] Relationships support diagram clarity
   - [ ] Relationship descriptions are clear

4. **Consistency:**
   - [ ] Relationships align with project type patterns
   - [ ] Bidirectional relationships are correctly represented
   - [ ] No contradictory relationships

**Instructions:**
- Validate all relationships against criteria
- Identify missing relationships
- Flag inappropriate or redundant relationships
- Suggest improvements

**Output:** Relationship validation report with specific feedback."""

SYSTEM_DIFF_ANALYSIS_MESSAGE = """You are analyzing code changes and their architectural impact.

**Role:** Assess how code changes affect system architecture and component design.

**Analysis Focus:**
1. Modified components and their boundaries
2. Changed relationships and dependencies
3. New or removed architectural elements
4. Impact on data flow and control flow
5. Design pattern changes

**Approach:**
- Compare before/after states
- Identify structural changes
- Assess architectural impact
- Consider implications for documentation and diagrams
- Focus on significant architectural changes

**Goal:** Clear understanding of architectural evolution and change impact."""

DIFF_ANALYSIS_MESSAGE = """Analyze architectural changes in the codebase.

**Context:**
Project: {project_name}
Changes: {diff_data}

**Task:** Identify and document architectural changes.

**Instructions:**
1. Analyze provided diff data
2. **Identify changes in:**
   - Component structure
   - Relationships and dependencies
   - Interfaces and APIs
   - Data flow patterns
   - Design patterns
3. **Assess impact:**
   - Magnitude of change (minor/moderate/major)
   - Affected components
   - Documentation update requirements
   - Diagram update requirements

**Output:**
1. Summary of architectural changes
2. Impact assessment
3. Affected components list
4. Recommended documentation updates

**Goal:** Clear understanding of how code changes affect system architecture."""

SYSTEM_META_ANALYSIS_MESSAGE = """You are performing meta-analysis on software project characteristics.

**Role:** Analyze project-level patterns, conventions, and architectural decisions.

**Analysis Areas:**
1. **Project Structure:**
   - Directory organization
   - Module layout patterns
   - File naming conventions

2. **Architectural Patterns:**
   - Design patterns in use
   - Architectural styles (MVC, microservices, etc.)
   - Common practices

3. **Technology Stack:**
   - Primary languages and frameworks
   - Dependencies and libraries
   - Build and deployment patterns

4. **Code Organization:**
   - Separation of concerns
   - Abstraction levels
   - Code reuse patterns

**Goal:** High-level understanding of project characteristics to inform detailed analysis."""

META_INFORMATION_PROMPT = """Extract meta-information about the project.

**Task:** Gather high-level project characteristics.

**Information to Extract:**
1. **Project Type:** Web app, library, CLI tool, microservice, etc.
2. **Primary Language(s):** Main programming languages used
3. **Frameworks:** Major frameworks and libraries
4. **Architecture Style:** MVC, microservices, layered, etc.
5. **Project Scale:** Small/medium/large (based on file count, LOC)
6. **Organization Patterns:** Module structure, naming conventions
7. **Key Technologies:** Databases, APIs, external services

**Instructions:**
- Use `getFileStructure` to understand directory organization
- Use `getPackageDependencies` to identify key dependencies
- Analyze file names and paths for patterns
- Identify technology stack from imports and dependencies

**Output:**
Structured meta-information summary suitable for context in subsequent analysis.

**Goal:** Provide context that improves the quality of architectural analysis."""

FILE_CLASSIFICATION_MESSAGE = """Classify files by their architectural role in the project.

**Task:** Categorize files into architectural roles.

**Classification Categories:**
1. **Core Business Logic:** Main application logic and domain models
2. **Infrastructure:** Database, networking, external services
3. **UI/Presentation:** User interface components, views, templates
4. **Configuration:** Settings, environment configs, build files
5. **Utilities:** Helper functions, common utilities, shared code
6. **Tests:** Test files and test utilities
7. **Documentation:** README, docs, comments
8. **Build/Deploy:** Build scripts, deployment configs, CI/CD
9. **External/Generated:** Third-party code, generated files

**Instructions:**
1. Analyze file paths, names, and extensions
2. Use `readFile` if classification is unclear from path alone
3. Assign primary category (and secondary if applicable)
4. Provide brief justification

**File List:**
{files}

**Output:**
For each file:
- File path
- Primary category
- Secondary category (if applicable)
- Brief justification

**Goal:** Understand file organization to inform component analysis and diagram generation."""


class GPTBidirectionalPromptFactory(AbstractPromptFactory):
    """Prompt factory for GPT-4 bidirectional mode."""

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
