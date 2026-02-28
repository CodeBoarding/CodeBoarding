"""
Prompt templates for OpenAI GPT-4 family models.

GPT-4 Prompt Design Principles:
    - Uses bold markdown headings and structured sections (**Role:**, **Responsibilities:**, **Approach:**)
      because GPT-4 responds well to explicit role definition and clearly labeled instruction blocks.
    - Employs detailed checklists (- [ ] items) for validation tasks. GPT-4 tends to be thorough when
      given an explicit checklist to work through, reducing the chance of skipped criteria.
    - Prompts are more verbose and descriptive than other models require. GPT-4 benefits from explicit
      context and detailed instructions; overly terse prompts can lead to GPT-4 making assumptions or
      filling gaps with generic responses rather than sticking to the task.
    - Expansion and planning prompts include explicit tool lists (readFile, getClassHierarchy, etc.)
      because GPT-4 is less likely to proactively discover and use tools unless explicitly told which
      ones are available and when to use them.
"""

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

Focus on:
- Creating cohesive, logical groupings that reflect the actual {project_type} architecture
- Semantic meaning based on method names, call patterns, and architectural context
- Clear justification for why clusters belong together
- Describing inter-group interactions based on the inter-cluster connections

Output Format:
Return a ClusterAnalysis with cluster_components using ClustersComponent model.
Each component should have name (descriptive label), cluster_ids (list), and description (comprehensive explanation with rationale and inter-group interactions)."""

FINAL_ANALYSIS_MESSAGE = """Create final component architecture for `{project_name}` optimized for flow representation.

Project Context:
{meta_context}

Cluster Analysis:
{cluster_analysis}

Instructions:
1. Review the named cluster groups above
2. Decide which named groups should be merged into final components
3. For each component, specify which named cluster groups it encompasses via source_group_names
4. Add key entities (2-5 most important classes/methods) for each component using SourceCodeReference
5. Define relationships between components

Guidelines for {project_type} projects:
- Aim for 5-8 final components
- Merge related cluster groups that serve a common purpose
- Each component should have clear boundaries
- Include only architecturally significant relationships

Required outputs:
- Description: One paragraph explaining the main flow and purpose
- Components: Each with:
  * name: Clear component name
  * description: What this component does
  * source_group_names: Which named cluster groups from the analysis above this component encompasses (use exact group names)
  * key_entities: 2-5 most important classes/methods (SourceCodeReference objects with qualified_name and reference_file)
- Relations: Max 2 relationships per component pair (avoid relations in which we have sends/returns i.e. ComponentA sends a message to ComponentB and ComponentB returns result to ComponentA)

Constraints:
- Focus on highest level architectural components
- Exclude utility/logging components
- Components should translate well to flow diagram representation"""

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
   - [ ] No redundant relationships (max 2 per pair (avoid relations in which we have sends/returns i.e. ComponentA sends a message to ComponentB and ComponentB returns result to ComponentA))

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
   - [ ] Maximum 2 relationships per component pair (avoid relations in which we have sends/returns i.e. ComponentA sends a message to ComponentB and ComponentB returns result to ComponentA)
   - [ ] Relationships support diagram clarity
   - [ ] Relationship descriptions are clear

4. **Consistency:**
   - [ ] Relationships align with project type patterns
   - [ ] Relationships are correctly represented
   - [ ] No contradictory relationships

**Instructions:**
- Validate all relationships against criteria
- Identify missing relationships
- Flag inappropriate or redundant relationships
- Suggest improvements

**Output:** Relationship validation report with specific feedback."""

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
- Use `readDocs` to understand project purpose and domain from documentation
- Use `getFileStructure` to understand directory organization
- Use `readExternalDeps` to identify dependency files and key frameworks
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

Focus on core subsystem functionality only. Avoid cross-cutting concerns like logging or error handling.

Output Format:
Return a ClusterAnalysis with cluster_components using ClustersComponent model.
Each component should have name (descriptive label), cluster_ids (list), and description (comprehensive explanation with rationale and inter-group interactions)."""

DETAILS_MESSAGE = """Create final sub-component architecture for the `{component}` subsystem of `{project_name}` optimized for flow representation.

Project Context:
{meta_context}

Cluster Analysis:
{cluster_analysis}

Instructions:
1. Review the named cluster groups above
2. Decide which named groups should be merged into final sub-components
3. For each sub-component, specify which named cluster groups it encompasses via source_group_names
4. Add key entities (2-5 most important classes/methods) for each sub-component using SourceCodeReference
5. Define relationships between sub-components

Guidelines for {project_type} projects:
- Aim for 3-8 final sub-components
- Merge related cluster groups that serve a common purpose
- Each sub-component should have clear boundaries
- Include only architecturally significant relationships

Required outputs:
- Description: One paragraph explaining the subsystem's main flow and purpose
- Components: Each with:
  * name: Clear sub-component name
  * description: What this sub-component does
  * source_group_names: Which named cluster groups from the analysis above this sub-component encompasses (use exact group names)
  * key_entities: 2-5 most important classes/methods (SourceCodeReference objects with qualified_name and reference_file)
- Relations: Max 2 relationships per component pair (avoid relations in which we have sends/returns i.e. ComponentA sends a message to ComponentB and ComponentB returns result to ComponentA)

Constraints:
- Focus on subsystem-specific functionality
- Exclude utility/logging sub-components
- Sub-components should translate well to flow diagram representation

Justify component choices based on fundamental architectural importance."""


class GPTPromptFactory(AbstractPromptFactory):
    """Prompt factory for GPT-4 models."""

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
