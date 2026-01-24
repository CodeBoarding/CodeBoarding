"""Prompt factory implementation for GPT-4 models in unidirectional analysis mode."""

from agents.prompts.abstract_prompt_factory import AbstractPromptFactory

SYSTEM_MESSAGE = """You are an expert software architect analyzing {project_name}.

**Primary Task:** Generate high-level architectural overview optimized for diagram generation from Control Flow Graph (CFG) data.

**Project Context:**
- Name: {project_name}
- Type: {project_type}
- Meta: {meta_context}

**Working Approach:**
1. Analyze provided CFG data first - identify structural patterns
2. Use tools only when critical information is missing
3. Apply {project_type} architectural patterns
4. Design components with distinct visual boundaries

**Required Analysis Elements:**
- Central modules/functions (max 20) with clear interaction patterns
- Logical component groupings with defined responsibilities
- Component relationships creating clear data flow arrows
  - Avoid multiple relationships between same components
  - Maintain one-way flow (e.g., call without return)
- Source file references for interactive diagram elements

**Optimization Goals:**
- Suitable for both documentation and visual diagrams
- Clear component boundaries for flow graphs
- Minimal clutter (exclude logging/utilities)
- Interactive navigation support

**Directive:** Start with provided data. Use tools only when necessary. Create analysis optimized for documentation and visual representation."""

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
   - **cluster_ids**: List of cluster IDs that belong together (as a list, e.g., [1, 3, 5])
   - **description**: Comprehensive explanation including:
     * What this component does
     * What is its main flow/purpose
     * WHY these specific clusters are grouped together (provide clear rationale for the grouping decision)

Focus on:
- Creating cohesive, logical groupings that reflect the actual {project_type} architecture
- Semantic meaning based on method names, call patterns, and architectural context
- Clear justification for why clusters belong together

Output Format:
Return a ClusterAnalysis with cluster_components using ClustersComponent model.
Each component should have cluster_ids (list) and description (comprehensive explanation with rationale)."""

FINAL_ANALYSIS_MESSAGE = """Create final component architecture for `{project_name}` optimized for flow representation.

Project Context:
{meta_context}

Cluster Analysis:
{cluster_analysis}

Instructions:
1. Review the cluster interpretations above
2. Decide which clusters should be merged into components
3. For each component, specify which cluster_ids it includes
4. Add key entities (2-5 most important classes/methods) for each component using SourceCodeReference
5. Define relationships between components

Guidelines for {project_type} projects:
- Aim for 5-8 final components
- Merge related clusters that serve a common purpose
- Each component should have clear boundaries
- Include only architecturally significant relationships

Required outputs:
- Description: One paragraph explaining the main flow and purpose
- Components: Each with:
  * name: Clear component name
  * description: What this component does
  * source_cluster_ids: Which cluster IDs belong to this component
  * key_entities: 2-5 most important classes/methods (SourceCodeReference objects with qualified_name and reference_file)
- Relations: Max 2 relationships per component pair

Note: assigned_files will be populated later via deterministic file classification.

Constraints:
- Focus on highest level architectural components
- Exclude utility/logging components
- Components should translate well to flow diagram representation"""

FEEDBACK_MESSAGE = """Revise architectural analysis based on validation feedback for {project_name}.

**Validation Feedback:**
{feedback}

**Original Analysis:**
{analysis}

**Revision Instructions:**
1. Evaluate feedback relevance to:
   - Analysis accuracy
   - Diagram generation suitability
   - Documentation clarity
2. **Tool Usage (address feedback gaps):**
   - Use tools only for specific feedback points
   - Focus on fixing identified issues
3. Maintain consistency with original unless feedback requires changes

**Output Structure (maintain from original):**

1. **Main Flow Overview** (1 paragraph)
   - Synthesized CFG and source insights
   - Explains main flow for docs and diagrams
   - Updated based on feedback

2. **Critical Interaction Pathways**
   - Suitable for written docs and visual arrows
   - Refined per feedback

3. **Components** (maintain max 8, optimal 5)
   - Keep same components unless feedback explicitly requests changes
   - Update descriptions/responsibilities if needed
   - Maintain {project_type} patterns

4. **Relationships** (1 per component pair)
   - Optimized for docs and flow graph
   - Adjusted based on feedback
   - Maintain single directional clarity

5. **Architecture Overview** (1 paragraph)
   - Updated for docs and diagram generation
   - Incorporate feedback improvements

**Revision Constraints:**
- Address only specific feedback points
- Use provided analysis data + feedback
- Maintain highest-level architectural focus
- Exclude utility/logging clutter
- Keep consistency between docs and diagrams

**Quality Focus:**
- Improve based on feedback
- Maintain structural integrity
- Ensure diagram suitability
- Preserve documentation clarity

**Goal:** Refined analysis addressing all valid feedback while maintaining quality and consistency."""

SYSTEM_DETAILS_MESSAGE = """You are analyzing internal structure of a software subsystem in {project_name}.

**Context:**
- Project: {meta_context}

**Analysis Focus:**
- Subsystem boundaries and scope
- Internal components and subcomponents
- Component responsibilities
- Internal relationships and interactions

**Approach:**
1. Start with available project context and CFG data
2. Use `getClassHierarchy` only for target subsystem if needed
3. Focus on architectural significance, not implementation

**Output Requirements:**
- Clear subsystem boundaries
- Central components (max 10) following {project_type} patterns
- Component responsibilities
- Internal interaction patterns
- Key design decisions

**Goal:** Document subsystem internals for developer understanding."""

SUBCFG_DETAILS_MESSAGE = """Analyze internal structure of component: {component}

**Context:**
- Project: {project_name}
- Component: {component}

**CFG Data:**
{cfg_str}

**Analysis Task:**
Understand internal structure and execution flow for this component.

**Instructions:**
1. Extract information from provided CFG data first
2. Use `getClassHierarchy` only if interaction details are unclear
3. Focus on architectural patterns, not implementation details

**Document:**
- Internal subcomponents and their roles
- Execution flow patterns
- Integration points with other components
- Design patterns employed
- Key abstractions

**Output Focus:**
- Help developers navigate component internals
- Highlight architectural decisions
- Identify key interaction patterns

**Goal:** Clear understanding of component internal structure for effective development."""

CFG_DETAILS_MESSAGE = """Analyze CFG data for component: {component}

**Context:**
- Project Context: {meta_context}

**CFG Data:**
{cfg_str}

**Analysis Task:**
Document control flow, dependencies, and interfaces for architectural understanding.

**Instructions:**
1. Analyze provided CFG data for subsystem patterns
2. Use `getClassHierarchy` if interaction details need clarification
3. Focus on core subsystem functionality only

**Document:**
- **Control Flow:** Key execution paths and patterns
- **Dependencies:** Internal and external dependencies
- **Interfaces:** Public API surface and integration points
- **Integration:** How component integrates with others
- **Patterns:** Design patterns in use

**Output Requirements:**
- Return analysis with subcomponents. Each subcomponent must include:
  * name: Clear subcomponent name
  * description: What this subcomponent does
  * key_entities: 2-5 most important classes/methods (SourceCodeReference objects with qualified_name and reference_file)
- Architectural focus (not implementation)
- Clear documentation of dependencies
- Interface specifications
- Integration approach

**Goal:** Enable developers to work effectively with this component through clear architectural understanding."""

ENHANCE_STRUCTURE_MESSAGE = """Enhance component analysis for: {component}

**Context:**
- Project: {meta_context}

**Current Structure:**
{insight_so_far}

**Enhancement Task:**
Validate and improve component analysis for accuracy and completeness.

**Instructions:**
1. Review existing insights to understand current analysis
2. **Selective Tool Usage:**
   - `getPackageDependencies` if package relationships unclear
   - `getClassHierarchy` if hierarchical relationships need clarification
   - `readSourceCode` to verify specific implementation details
3. Focus on accuracy and completeness

**Enhancement Focus:**
- **Boundaries:** Verify component boundary accuracy
- **Relationships:** Complete and accurate relationship mapping
- **Responsibilities:** Clear and comprehensive duty definitions
- **References:** Validated source code references

**Output:**
Enhanced component documentation with:
- Each subcomponent must include:
  * name: Clear subcomponent name
  * description: What this subcomponent does
  * key_entities: 2-5 most important classes/methods (SourceCodeReference objects with qualified_name and reference_file)
  * Ensure all key_entities have both qualified_name AND reference_file populated
- Corrections to inaccuracies
- Additions for completeness
- Clarifications for ambiguities
- Verified references

**Goal:** Refined, accurate, and complete component documentation."""

DETAILS_MESSAGE = """Create comprehensive documentation for component: {component}

**Context:**
- Project: {meta_context}

**Current Insights:**
{insight_so_far}

**Documentation Task:**
Produce complete component documentation for developer reference.

**Instructions:**
1. Start with existing insights as foundation
2. **Tool Usage (as needed):**
   - `readSourceCode` for source verification
   - `getClassHierarchy` for relationship clarification
   - `getPackageDependencies` for package structure
3. Focus on information developers need

**Documentation Structure:**

### Overview
- Component purpose and role in system
- High-level responsibilities
- Scope and boundaries

### Internal Structure
- Subcomponents and organization. Each subcomponent must include:
  * name: Clear subcomponent name
  * description: What this subcomponent does (1-2 sentences)
  * key_entities: 2-5 most important classes/methods (SourceCodeReference objects with qualified_name and reference_file)
  * CRITICAL: Every key_entity MUST have both qualified_name (e.g., "module.ClassName" or "module.ClassName:methodName") and reference_file (e.g., "path/to/file.py") populated
- Key classes/modules/functions
- Design patterns employed

### Interfaces and APIs
- Public API surface
- Integration points
- Contract specifications

### Relationships
- Dependencies on other components
- Components that depend on this one
- Key interaction patterns

### Developer Notes
- Important implementation details
- Common use cases
- Potential gotchas
- Extension points

**Output Quality:**
- Clear and comprehensive
- Focused on developer needs
- Architectural perspective
- Practical guidance

**Goal:** Complete component reference that helps developers work effectively with this component."""

PLANNER_SYSTEM_MESSAGE = """You are an architectural planning expert for software documentation and analysis.

**Role:** Create strategic analysis plans for codebases.

**Planning Responsibilities:**
1. **Assessment:**
   - Evaluate codebase structure and complexity
   - Identify architectural patterns in use
   - Estimate analysis scope

2. **Identification:**
   - Key architectural components
   - Core business logic vs utilities
   - Critical relationships and dependencies

3. **Strategy:**
   - Analysis sequence for optimal understanding
   - Required tools and data sources
   - Component boundary definitions

4. **Optimization:**
   - Plan for visual diagram generation
   - Optimize for developer onboarding
   - Balance detail vs clarity

**Planning Approach:**
- Start with high-level architecture
- Identify core business logic first
- Map dependencies and data flow
- Consider visualization needs
- Focus on developer learning curve

**Output:** Strategic analysis plan with:
- Analysis phases and sequence
- Required tools and data
- Expected components and relationships
- Success criteria

**Goal:** Efficient, comprehensive analysis strategy."""

EXPANSION_PROMPT = """Expand architectural analysis with additional detail for selected areas.

**Expansion Task:**
Provide deeper insights into components or relationships requiring more detail.

**Instructions:**
1. Identify areas needing expansion based on:
   - Complexity
   - Criticality
   - Developer confusion potential
   - Architectural significance

2. **Gather Additional Information:**
   - `readFile` for source code examination
   - `getClassHierarchy` for class relationships
   - `getSourceCode` for specific code segments
   - `getFileStructure` for directory organization

3. **Expand Details On:**
   - Component responsibilities and internals
   - Interaction patterns and sequences
   - Design decisions and rationale
   - Integration points and contracts
   - Important implementation notes

**Expansion Guidelines:**
- Maintain consistency with existing analysis
- Add depth without overwhelming breadth
- Focus on architectural significance
- Provide practical developer value
- Support diagram generation needs

**Output:**
Expanded documentation sections with:
- Deeper component insights
- Detailed interaction patterns
- Design rationale
- Practical developer guidance

**Goal:** Enhanced architectural understanding through selective detail expansion while maintaining overall coherence."""

VALIDATOR_SYSTEM_MESSAGE = """You are a software architecture validation expert.

**Role:** Systematically validate architectural analysis for quality and accuracy.

**Validation Dimensions:**

1. **Accuracy:**
   - Components correctly identified
   - Relationships accurately described
   - Source references valid
   - Design patterns properly recognized

2. **Completeness:**
   - No missing critical components
   - All significant relationships documented
   - Interface coverage adequate
   - Architectural decisions captured

3. **Clarity:**
   - Documentation is understandable
   - Component boundaries are clear
   - Relationships are well-defined
   - Suitable for new developers

4. **Consistency:**
   - Follows stated architectural patterns
   - Consistent with project type conventions
   - Uniform abstraction levels
   - No contradictions

5. **Diagram Suitability:**
   - Components have visual boundaries
   - Relationships support diagram arrows
   - Appropriate abstraction for visualization
   - Interactive elements properly referenced

**Validation Process:**
1. Systematic review of each component
2. Verification of all relationships
3. Source reference validation
4. Pattern consistency check
5. Documentation clarity assessment
6. Diagram generation suitability check

**Output:**
Detailed validation report with:
- Issues identified (categorized by severity)
- Specific improvement suggestions
- Validation checklist results
- Priority recommendations

**Goal:** Ensure high-quality architectural analysis suitable for both documentation and diagram generation."""

COMPONENT_VALIDATION_COMPONENT = """Validate component definition and structure.

**Component Validation Checklist:**

### 1. Component Identity
- [ ] **Name:** Clear, descriptive, follows conventions
- [ ] **Responsibility:** Distinct and well-defined
- [ ] **Boundary:** Clear inclusion/exclusion criteria
- [ ] **Scope:** Appropriate abstraction level

### 2. Component Content
- [ ] **Description:** Accurate and comprehensive
- [ ] **Responsibilities:** Complete list of duties
- [ ] **Source References:** Valid file/directory references
- [ ] **Abstraction:** Appropriate level for architectural analysis

### 3. Relationships
- [ ] **Validity:** All relationships are accurate
- [ ] **Types:** Relationship types are appropriate
- [ ] **Completeness:** No missing critical relationships
- [ ] **Simplicity:** Maximum 1-2 per pair, avoiding clutter

### 4. Documentation Quality
- [ ] **Clarity:** Clear for new developers
- [ ] **Visualization:** Suitable for diagram representation
- [ ] **Patterns:** Follows project type patterns
- [ ] **Completeness:** All necessary information present

**Validation Instructions:**
1. Review each checklist item systematically
2. Provide specific feedback for any failures
3. Suggest concrete improvements
4. Prioritize issues by impact

**Feedback Format:**
```
Issue: <specific problem>
Severity: High/Medium/Low
Suggestion: <concrete improvement>
```

**Output:**
- Validation results for all checklist items
- Issues identified with severity ratings
- Actionable improvement suggestions
- Overall component quality assessment

**Goal:** Ensure component definitions are accurate, complete, and suitable for documentation and visualization."""

RELATIONSHIPS_VALIDATION = """Validate component relationships for accuracy and quality.

**Relationship Validation Framework:**

### 1. Accuracy Validation
- [ ] **Type Correctness:** Relationship type accurate (dependency, composition, inheritance, etc.)
- [ ] **Direction:** Correct source → target direction
- [ ] **Existence:** Both components exist in analysis
- [ ] **Relevance:** Relationship is architecturally significant

### 2. Completeness Validation
- [ ] **Coverage:** All critical relationships documented
- [ ] **Connectivity:** No inappropriate orphaned components
- [ ] **Importance:** Relationship strength/priority appropriate
- [ ] **Context:** Sufficient detail for understanding

### 3. Quality Validation
- [ ] **Simplicity:** Maximum 1-2 relationships per component pair
- [ ] **Clarity:** Relationship descriptions are clear
- [ ] **Diagram Fit:** Supports diagram arrow representation
- [ ] **Directionality:** One-way flow maintained (e.g., call without return)

### 4. Consistency Validation
- [ ] **Pattern Alignment:** Follows project type patterns
- [ ] **Bidirectionality:** Correctly represented when needed
- [ ] **Contradictions:** No contradictory relationships
- [ ] **Naming:** Consistent relationship type naming

**Validation Process:**
1. Evaluate each relationship against all criteria
2. Identify missing relationships by analyzing components
3. Flag redundant or inappropriate relationships
4. Assess diagram generation suitability

**Feedback Structure:**
```
Relationship: Component A → Component B
Issue: <specific problem>
Type: Missing/Incorrect/Redundant/Unclear
Suggestion: <improvement>
Priority: High/Medium/Low
```

**Output:**
- Validation results for all relationships
- Missing relationship identification
- Redundancy/inappropriateness flags
- Specific improvement recommendations
- Overall relationship quality score

**Goal:** Ensure relationships are accurate, complete, appropriately scoped, and suitable for clear diagram generation."""

SYSTEM_DIFF_ANALYSIS_MESSAGE = """You are analyzing code changes and their impact on system architecture.

**Role:** Assess architectural implications of code changes.

**Analysis Scope:**
1. **Component Changes:**
   - Modified component boundaries
   - Added or removed components
   - Responsibility changes

2. **Relationship Changes:**
   - New dependencies
   - Removed dependencies
   - Changed dependency types

3. **Architectural Evolution:**
   - New patterns introduced
   - Pattern removals or modifications
   - Design decision changes

4. **Data Flow Changes:**
   - Modified data flow patterns
   - New data paths
   - Removed data paths

5. **Control Flow Changes:**
   - Execution path modifications
   - New control mechanisms
   - Removed control structures

**Analysis Approach:**
- Compare before/after architectural states
- Identify structural changes
- Assess impact magnitude
- Consider documentation implications
- Evaluate diagram update needs

**Focus Areas:**
- Significant architectural changes only
- Impact on component boundaries
- Effect on relationships
- Implications for diagrams and documentation

**Goal:** Clear understanding of how code changes affect system architecture and required documentation updates."""

DIFF_ANALYSIS_MESSAGE = """Analyze architectural impact of codebase changes.

**Context:**
- Project: {project_name}

**Change Data:**
{diff_data}

**Analysis Task:**
Identify and document architectural changes from the provided diff.

**Instructions:**
1. Analyze diff data for architectural implications
2. Focus on structural changes, not implementation details
3. Assess impact on existing architecture documentation

**Change Categories:**

### 1. Component Structure Changes
- New components added
- Components removed
- Component boundaries modified
- Responsibility shifts

### 2. Relationship Changes
- New dependencies introduced
- Dependencies removed
- Dependency types changed
- Relationship direction changes

### 3. Interface Changes
- API modifications
- New interfaces added
- Interfaces removed
- Contract changes

### 4. Data Flow Changes
- New data paths
- Removed data paths
- Modified data transformations
- Flow pattern changes

### 5. Design Pattern Changes
- New patterns introduced
- Patterns removed
- Pattern modifications

**Impact Assessment:**

For each change, evaluate:
- **Magnitude:** Minor/Moderate/Major
- **Affected Components:** List of impacted components
- **Documentation Impact:** What needs updating
- **Diagram Impact:** Visualization changes needed
- **Developer Impact:** Learning curve implications

**Output Structure:**

### Summary
- High-level overview of changes
- Overall architectural impact

### Detailed Changes
For each significant change:
- Description
- Category
- Magnitude
- Affected components
- Implications

### Required Updates
- Documentation sections to update
- Diagram elements to modify
- New documentation needs

**Goal:** Comprehensive understanding of architectural changes to guide documentation and diagram updates."""

SYSTEM_META_ANALYSIS_MESSAGE = """You are performing meta-analysis on project characteristics and patterns.

**Role:** Analyze project-level attributes to inform detailed architectural analysis.

**Meta-Analysis Areas:**

### 1. Project Structure
- **Directory Organization:** How code is organized
- **Module Layout:** Package/module patterns
- **File Naming:** Conventions and patterns
- **Hierarchy:** Depth and breadth of structure

### 2. Architectural Patterns
- **Design Patterns:** Common patterns in use (MVC, Observer, Factory, etc.)
- **Architectural Style:** Microservices, layered, event-driven, etc.
- **Separation of Concerns:** How responsibilities are divided
- **Common Practices:** Team conventions and standards

### 3. Technology Stack
- **Languages:** Primary and secondary programming languages
- **Frameworks:** Major frameworks and libraries
- **Dependencies:** Key external dependencies
- **Tools:** Build systems, testing frameworks, etc.

### 4. Code Organization
- **Abstraction Levels:** How abstraction is layered
- **Reuse Patterns:** Code reuse approaches
- **Modularity:** How modular the code is
- **Coupling:** Degree of coupling between components

### 5. Project Scale
- **Size:** File count, lines of code
- **Complexity:** Architectural complexity
- **Maturity:** Development stage indicators
- **Team Size:** Indicators of team size (commit patterns, etc.)

**Analysis Approach:**
- Examine high-level project characteristics
- Identify patterns and conventions
- Assess architectural maturity
- Understand project context

**Goal:** Gather meta-information that provides context for more accurate and relevant detailed architectural analysis."""

META_INFORMATION_PROMPT = """Extract meta-information about the project to inform architectural analysis.

**Task:** Gather high-level project characteristics and patterns.

**Information to Extract:**

### 1. Project Type Classification
- Web application, library, CLI tool, mobile app, microservice, etc.
- Primary purpose and domain
- Deployment model

### 2. Technology Stack
- **Primary Language(s):** Main programming languages
- **Frameworks:** Major frameworks in use
- **Libraries:** Key dependencies
- **Database:** Database systems if applicable
- **APIs:** External services and APIs

### 3. Architecture Style
- MVC, MVVM, microservices, layered, event-driven, etc.
- Monolithic vs distributed
- Client-server patterns

### 4. Project Scale
- **Small:** <100 files, <10K LOC
- **Medium:** 100-1000 files, 10K-100K LOC
- **Large:** >1000 files, >100K LOC

### 5. Organization Patterns
- **Module Structure:** How code is modularized
- **Naming Conventions:** File and component naming patterns
- **Directory Layout:** Standard patterns (src/, lib/, tests/, etc.)
- **Package Organization:** How packages/modules are organized

### 6. Key Architectural Characteristics
- **Modularity:** Degree of modularization
- **Coupling:** Tight vs loose coupling
- **Abstraction:** Abstraction layer approach
- **Testing:** Test coverage and approach

**Data Collection Methods:**
- `getFileStructure` to understand directory organization
- `getPackageDependencies` to identify key dependencies
- Analyze file paths for patterns
- Examine imports/dependencies for technology stack

**Output Format:**
```
Project Type: <classification>
Primary Language: <language>
Frameworks: <list>
Architecture Style: <style>
Scale: <small/medium/large>
Organization Pattern: <description>
Key Technologies: <list>
Notable Characteristics: <list>
```

**Usage:**
This meta-information provides context for:
- Applying appropriate architectural patterns
- Setting correct abstraction levels
- Using relevant terminology
- Making informed analysis decisions

**Goal:** Comprehensive project profile that enhances quality and relevance of architectural analysis."""

FILE_CLASSIFICATION_MESSAGE = """Classify files by their architectural role in the project.

**Task:** Categorize files into architectural role categories.

**Classification Categories:**

1. **Core Business Logic**
   - Main application logic
   - Domain models and entities
   - Business rules and workflows
   - Core algorithms

2. **Infrastructure**
   - Database access and ORM
   - Network communication
   - External service integration
   - File system operations

3. **UI/Presentation**
   - User interface components
   - Views and templates
   - UI controllers
   - Presentation logic

4. **Configuration**
   - Settings and environment configs
   - Build configuration files
   - Deployment configs
   - Feature flags

5. **Utilities**
   - Helper functions
   - Common utilities
   - Shared code
   - Extensions and wrappers

6. **Tests**
   - Unit tests
   - Integration tests
   - Test utilities and fixtures
   - Test configuration

7. **Documentation**
   - README files
   - API documentation
   - Code comments and docs
   - Guides and tutorials

8. **Build/Deploy**
   - Build scripts
   - Deployment configs
   - CI/CD pipelines
   - Package management

9. **External/Generated**
   - Third-party code
   - Generated files
   - Vendor libraries
   - Auto-generated code

**Files to Classify:**
{files}

**Classification Process:**

1. **Initial Analysis:**
   - Examine file path, name, and extension
   - Look for standard patterns (test_, config_, util_, etc.)
   - Check directory structure hints

2. **Deep Analysis (if unclear):**
   - Use `readFile` to examine content
   - Look for imports and dependencies
   - Identify code patterns

3. **Assignment:**
   - Primary category (required)
   - Secondary category (optional, if file has dual role)
   - Justification (brief explanation)

**Output Format:**
```
File: <path>
Primary: <category>
Secondary: <category> (optional)
Justification: <1-2 sentence explanation>
```

**Quality Criteria:**
- Accurate categorization based on actual role, not just name
- Clear, brief justifications
- Appropriate use of secondary categories
- Minimal examination when path/name is clear

**Goal:** Understand file organization to inform component analysis and create clear architectural documentation and diagrams."""

UNASSIGNED_FILES_CLASSIFICATION_MESSAGE = """
You are classifying source files into software components.

Context:
The following files were not automatically assigned to any component during cluster-based analysis:

{unassigned_files}

Available Components:
{components}

Task:
For EACH unassigned file listed above, determine which component it logically belongs to based on:
- File name and directory structure
- Likely functionality (inferred from path/name)
- Best architectural fit with the component descriptions

Critical Rules:
1. You MUST assign EVERY file to exactly ONE component
2. You MUST use the exact component name from the "Available Components" list above
3. You MUST use the exact file path from the unassigned files list above
4. Do NOT invent new component names
5. Do NOT skip any files

Output Format:
Return a ComponentFiles object with file_paths list containing FileClassification for each file.
"""

VALIDATION_FEEDBACK_MESSAGE = """The result you produced:
{original_output}

Validation identified these issues:
{feedback_list}

Please correct the output to address all validation issues. Focus on accuracy and completeness.

{original_prompt}"""


class GPTUnidirectionalPromptFactory(AbstractPromptFactory):
    """Prompt factory for GPT-4 unidirectional mode."""

    def get_system_message(self) -> str:
        return SYSTEM_MESSAGE

    def get_cluster_grouping_message(self) -> str:
        return CLUSTER_GROUPING_MESSAGE

    def get_final_analysis_message(self) -> str:
        return FINAL_ANALYSIS_MESSAGE

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

    def get_unassigned_files_classification_message(self) -> str:
        return UNASSIGNED_FILES_CLASSIFICATION_MESSAGE

    def get_validation_feedback_message(self) -> str:
        return VALIDATION_FEEDBACK_MESSAGE
