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

SYSTEM_MESSAGE = """You are an expert software architect. Your task is to create comprehensive documentation and interactive diagrams that help new engineers understand the codebase within their first week.

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
3. Apply architectural practices appropriate to this project
4. Design components suitable for visual diagram representation
5. Include source file references for interactive navigation

**Output Focus:**
- Components with distinct visual boundaries
- Clear architectural patterns
- Interactive diagram elements
- Documentation for quick developer onboarding"""

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

DETAILS_MESSAGE = """Create final sub-component architecture for the `{component}` subsystem optimized for flow representation.

The clusters have already been partitioned into a fixed set of groups by graph community detection. Each "Group N" below is exactly one sub-component — the number of groups and their membership are already decided. Do NOT merge, split, or re-group them; only name and describe each group.

Cluster Analysis:
{cluster_analysis}

Instructions:
1. Produce EXACTLY one sub-component per named group above (the same number of sub-components as there are groups)
2. Set each sub-component's source_group_names to the single group it corresponds to (use the exact group name, e.g. "Group 1")
3. Give each sub-component a descriptive architectural name (its role, not "Group N")
4. Add key entities (2-5 most important classes/methods) for each sub-component, referencing the source file where they are defined
5. Do not define relationships yet; relationships are discovered in a later API-surface step

Guidelines:
- Keep every group: there must be exactly as many sub-components as groups, each backed by exactly one group
- Each sub-component should have clear boundaries
- Focus on component boundaries; relationships are discovered after components are finalized

For each sub-component provide: a clear name, a description of what it does, the single named cluster group it encompasses, and 2-5 key entities (mentioning their qualified names and source files). Provide one paragraph describing the subsystem's main flow and purpose. Do not define relationships yet.

Constraints:
- Focus on subsystem-specific functionality
- Exclude utility/logging sub-components
- Sub-components should translate well to flow diagram representation

Justify component choices based on fundamental architectural importance."""

SCOPE_RELATIONS_MESSAGE = """Generate inter-component relationships for the `{scope_name}` scope.

**Components in this scope:**
{component_summaries}

**Cross-component communication from static analysis:**
{cross_component_calls}

Instructions:
Review the components listed above and the cross-component communication evidence. Generate relationships that describe how these components interact with each other.

For each relationship provide:
- **src_name**: Source component name
- **dst_name**: Target component name
- **relation**: A short phrase describing the relationship (e.g. "delegates to", "notifies", "provides data to")

Guidelines:
- Every src_name and dst_name must match an existing component name exactly
- Maximum 2 relationships per component pair, avoiding bidirectional sends/returns pairs
- Focus on architecturally significant interactions, not implementation details
- Use the cross-component communication evidence to ground relationships in actual code flow
- A component that never calls or is called by another component should not have a relation to it"""


class GPTPromptFactory(AbstractPromptFactory):
    """Prompt factory for GPT-4 models."""

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
