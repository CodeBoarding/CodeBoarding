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

CLUSTER_GROUPING_MESSAGE = """Analyze and GROUP the Control Flow Graph clusters.

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
     * The most important classes/methods in this group — mention their exact qualified names as shown in the clusters above

Focus on:
- Creating cohesive, logical groupings that reflect the actual architecture
- Semantic meaning based on method names, call patterns, and architectural context
- Clear justification for why clusters belong together
- Describing inter-group interactions based on the inter-cluster connections"""

FINAL_ANALYSIS_MESSAGE = """Create final component architecture optimized for flow representation.

Cluster Analysis:
{cluster_analysis}

Instructions:
1. Review the named cluster groups above
2. Decide which named groups should be merged into final components
3. For each component, specify which named cluster groups it encompasses (use exact group names from the analysis above)
4. Add 2-5 key entities (the most important classes/methods) for each component, mentioning their qualified names and source files
5. Do not define relationships yet; relationships are discovered in a later API-surface step
6. Provide a one-paragraph description of the overall main flow and purpose

Guidelines:
- Aim for 5-8 final components
- Merge related cluster groups that serve a common purpose
- Each component should have clear boundaries
- Focus on component boundaries; relationships are discovered after components are finalized

Constraints:
- Focus on highest level architectural components
- Exclude utility/logging components
- Components should translate well to flow diagram representation"""

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

CFG_DETAILS_MESSAGE = """Analyze and GROUP the Control Flow Graph clusters for the `{component}` subsystem.

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
     * The most important classes/methods in this group — mention their exact qualified names as shown in the clusters above

Focus on core subsystem functionality only. Avoid cross-cutting concerns like logging or error handling."""

DETAILS_MESSAGE = """Create final sub-component architecture for the `{component}` subsystem optimized for flow representation.

Cluster Analysis:
{cluster_analysis}

Instructions:
1. Review the named cluster groups above
2. Decide which named groups should be merged into final sub-components
3. For each sub-component, specify which named cluster groups it encompasses (use exact group names from the analysis above)
4. Add 2-5 key entities (the most important classes/methods) for each sub-component, mentioning their qualified names and source files
5. Do not define relationships yet; relationships are discovered in a later API-surface step
6. Provide a one-paragraph description of the subsystem's main flow and purpose

Guidelines:
- Aim for 3-8 final sub-components
- Merge related cluster groups that serve a common purpose
- Each sub-component should have clear boundaries
- Focus on component boundaries; relationships are discovered after components are finalized

Constraints:
- Focus on subsystem-specific functionality
- Exclude utility/logging sub-components
- Sub-components should translate well to flow diagram representation

Justify component choices based on fundamental architectural importance."""

INCREMENTAL_GROUPING_MESSAGE = """Update the architecture by routing changed and new CFG clusters to the right components.

<context>
The previous analysis established the components below. Most clusters are unchanged and stay where they are; this prompt only shows the structural slice that changed: new clusters, removed clusters, or clusters whose member set changed through added/removed methods. A method body edit by itself is not a cluster-boundary change.

### Existing components (each line shows `component_id "name"`)
{existing_components}

### Cluster groups to assign
{cfg_clusters}
</context>

<instructions>
Your Task:
Route each cluster shown above into the right component. Every cluster id must appear in exactly one routing entry.

For each cluster, there are two possibilities:

1. **Route to an existing component.** Match the cluster to one of the existing components listed above by its component id (e.g. "1.3"). Reuse that component's name and a short description verbatim, and collect the cluster ids that belong to it. Multiple groups of clusters can route to the same existing component — just make a separate entry for each group.

   Also judge whether the component's description needs updating. Set the **redetail_needed** flag to True (the default) when the cluster delta meaningfully changes what the component does — a new responsibility, a removed responsibility, or a semantic shift in its public surface. Set it to False only when the delta is purely cosmetic — an internal refactor, rename, small bug fix, or formatting change — and the component's high-level purpose is untouched. When False, the existing description is preserved as-is and no follow-up redetail runs. Bias toward True if you are uncertain.

2. **Create a new component.** Leave the existing component id as null, give the new component a fresh name (distinct from every existing component name), write a description paragraph explaining what this component does and why these clusters belong together, and choose a parent component id under which it should attach (or null for root). Pick the parent whose scope most naturally encloses the new component.

A critical correctness rule: identity is tracked by component id, not by name. If clusters belong in an existing component, you MUST reference its component id explicitly. Reusing an existing component's name without pointing to its component id will fork a duplicate — that is wrong.

Boundary rules:
- Route each changed cluster to the most specific owning component. If both a parent and a child seem relevant, choose the child only.
- `redetail_needed=False` means the component boundary is unchanged; do not use it to absorb new files, new responsibilities, or clusters owned by another component.

Focus on:
- Placing clusters where they belong architecturally, guided by method names, call patterns, and the existing component boundaries
- Creating cohesive groupings that reflect the actual architecture
- Accurately judging whether a delta is meaningful or cosmetic for the redetail decision
- Choosing sensible parents for new components based on scope and responsibility
</instructions>

<tool_usage_policy>
For each cluster you're uncertain about, you may read source. Keep each read small and targeted — the source of a single representative qname is usually the right unit. Continue reading further (still in small, focused steps) only while you remain uncertain about that specific cluster's placement, and stop as soon as your confidence is high. Don't broaden the scope of a single read to cover ground you don't yet need.
</tool_usage_policy>"""


PLANNING_MESSAGE = """Update one scope of the architecture diagram.

<context>
Scope: `{scope_id}` (`root` means the top-level diagram)

Existing components in this scope:
{existing_components}

Changed files:
{changed_files}

Structural cluster diff:
{structural_diff}

</context>

<instructions>
Return operations for this scope only.

- Keep unchanged clusters out of the operations unless the diff makes the component semantically dirty.
- For modified clusters, preserve the existing owning component shown by its clusters=[...] list; use update_component for that owner instead of moving the cluster to another component.
- For new clusters, decide from the structural diff whether they extend an existing responsibility or introduce a new component; do not infer this from file/package layout alone.
- For reshaped groups, follow overlap counts to keep old cluster ownership stable. Only assign a reshaped new cluster to a different component when the diff proves a real responsibility move.
- Reparenting existing components is unsupported by the current incremental schema. Preserve their current scope.
- Every modified/new/reshaped new-side cluster listed below must appear in exactly one operation's cluster_refs.

<architecture_output_contract>
- This step plans component boundaries only. Do not define component relations; API surfaces and relations are generated later.
- Choose exactly one of these mutually exclusive branches for each operation:
  - For create_component only: leave component_id null; provide a clear name and description. Select up to 5 key_entities only when their exact qualified names are available; otherwise leave them empty. Key entities are not synthesized later.
  - For update_component only: copy the exact component_id from the existing-components list. Include refreshed name, description, or key_entities only when the component's architectural responsibility changed; otherwise preserve the existing metadata. An empty key_entities list preserves the current selection.
  - For delete_component or noop only: copy the exact component_id from the existing-components list and leave name, description, and key_entities empty. Use delete_component only when the component has no remaining responsibility; use noop to preserve it unchanged.
</architecture_output_contract>

</instructions>

<tool_usage_policy>
Use listGitChanges only when the structural diff is not enough to judge semantic impact. Keep reads targeted to the uncertain cluster.
</tool_usage_policy>"""


class ClaudePromptFactory(AbstractPromptFactory):
    """Prompt factory for Claude models."""

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

    def get_incremental_grouping_message(self) -> str:
        return INCREMENTAL_GROUPING_MESSAGE

    def get_planning_message(self) -> str:
        return PLANNING_MESSAGE

    def get_scope_relations_message(self) -> str:
        return SCOPE_RELATIONS_MESSAGE

    def get_details_message(self) -> str:
        return DETAILS_MESSAGE
