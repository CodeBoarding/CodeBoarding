from __future__ import annotations

from pydantic import Field

from agents.llm_base import LLMBaseModel


class SourceCodeReference(LLMBaseModel):
    """Reference to source code including qualified name and file location."""

    qualified_name: str = Field(
        description="Qualified name of the source code, e.g., `langchain.tools.tool` or `langchain_core.output_parsers.JsonOutputParser` or `langchain_core.output_parsers.JsonOutputParser:parse`."
    )

    reference_file: str | None = Field(
        default=None,
        description="File path where the source code is located, e.g., `langchain/tools/tool.py` or `langchain_core/output_parsers/json_output_parser.py`.",
    )

    reference_start_line: int | None = Field(
        default=None,
        description="The line number in the source code where the reference starts. Only if you are absolutely sure add this, otherwise None.",
    )
    reference_end_line: int | None = Field(
        default=None,
        description="The line number in the source code where the reference ends. Only if you are absolutely sure add this, otherwise None.",
    )

    def llm_str(self):
        if self.reference_start_line is None or self.reference_end_line is None:
            return f"QName:`{self.qualified_name}` FileRef: `{self.reference_file}`"
        if (
            self.reference_start_line <= self.reference_end_line <= 0
            or self.reference_start_line == self.reference_end_line
        ):
            return f"QName:`{self.qualified_name}` FileRef: `{self.reference_file}`"
        return f"QName:`{self.qualified_name}` FileRef: `{self.reference_file}`, Lines:({self.reference_start_line}:{self.reference_end_line})"

    def __str__(self):
        if self.reference_start_line is None or self.reference_end_line is None:
            return f"`{self.qualified_name}`"
        if (
            self.reference_start_line <= self.reference_end_line <= 0
            or self.reference_start_line == self.reference_end_line
        ):
            return f"`{self.qualified_name}`"
        return f"`{self.qualified_name}`:{self.reference_start_line}-{self.reference_end_line}"


def _relation_str(rel) -> str:
    return f"({rel.src_name}, {rel.relation}, {rel.dst_name})"


class RelationLLM(LLMBaseModel):
    """A relationship between two components, as produced by the LLM."""

    relation: str = Field(description="Single phrase used for the relationship of two components.")
    src_name: str = Field(description="Source component name")
    dst_name: str = Field(description="Target component name")

    def llm_str(self):
        return _relation_str(self)


class ClustersComponent(LLMBaseModel):
    """A grouped component from cluster analysis - may contain multiple clusters."""

    name: str = Field(
        description="Short, descriptive name for this cluster group (e.g., 'Authentication', 'Data Pipeline', 'Request Handling')"
    )
    cluster_ids: list[int] = Field(
        description="List of cluster IDs from the CFG analysis that are grouped together (e.g., [1, 3, 5])"
    )
    description: str = Field(
        description="Explanation of what this component does, its main flow, WHY these clusters are grouped together, how it interacts with other cluster groups, and the most important classes/methods (by their exact qualified names from the clusters)"
    )
    existing_component_id: str | None = Field(
        default=None,
        description=(
            "Incremental routing: the exact component_id of the existing component "
            "this entry is routing clusters into (e.g. '1.3'). Set to null to create "
            "a brand-new component. Identity is by ID, not name — leaving this null "
            "while reusing an existing component's name forks a duplicate component. "
            "Ignored by the full-analysis flow."
        ),
        json_schema_extra={"hidden": True},
    )
    parent_id: str | None = Field(
        default=None,
        description=(
            "Incremental routing: when ``existing_component_id`` is null (brand-new "
            "component), the existing component_id under which the new component "
            "should attach (or null to attach at root). Ignored when "
            "``existing_component_id`` is set, and ignored by the full-analysis flow."
        ),
        json_schema_extra={"hidden": True},
    )
    redetail_needed: bool = Field(
        default=True,
        description=(
            "Incremental routing only: when routing clusters into an existing component "
            "(``existing_component_id`` is set), set False if the cluster delta is "
            "cosmetic (refactor, internal rename, small bug fix) and the component's "
            "high-level purpose is unchanged — the existing description stays. Default "
            "True forces a full redetail. Ignored for brand-new components (always "
            "redetailed) and by the full-analysis flow."
        ),
        json_schema_extra={"hidden": True},
    )

    def llm_str(self):
        ids_str = ", ".join(str(cid) for cid in self.cluster_ids)
        return f"**{self.name}** (cluster_ids: [{ids_str}])\n   {self.description}"


class ClusterAnalysis(LLMBaseModel):
    """Analysis results containing grouped cluster components."""

    cluster_components: list[ClustersComponent] = Field(
        description="Grouped clusters into logical components. Multiple cluster IDs can be grouped together if they work as a cohesive unit."
    )

    def llm_str(self):
        if not self.cluster_components:
            return "No clusters analyzed."
        title = "# Grouped Cluster Components\n"
        body = "\n".join(cc.llm_str() for cc in self.cluster_components)
        return title + body


def _component_str(comp) -> str:
    n = f"**Component:** `{comp.name}`"
    d = f"   - *Description*: {comp.description}"
    sg = ""
    if comp.source_group_names:
        sg = f"   - *Source Group Names*: {', '.join(comp.source_group_names)}"
    qn = ""
    if comp.key_entities:
        qn += "   - *Key Entities*: "
        qn += ", ".join(f"`{q.llm_str()}`" for q in comp.key_entities)
    return "\n".join([n, d, sg, qn]).strip()


class ComponentLLM(LLMBaseModel):
    """A software component as produced by the LLM: name, description, key entities."""

    name: str = Field(description="Name of the component")
    description: str = Field(description="A short description of the component.")

    # LLM picks these: The MOST IMPORTANT/critical methods and classes
    key_entities: list[SourceCodeReference] = Field(
        description="The most important/critical classes and methods that represent this component's core functionality. Pick 2-5 key entities."
    )

    source_group_names: list[str] = Field(
        description="Names of the cluster groups from the grouping analysis that this component encompasses.",
        default_factory=list,
    )

    def llm_str(self):
        return _component_str(self)


def _analysis_str(analysis) -> str:
    if not analysis.components:
        return "No abstract components found."
    title = "# Abstract Components Overview\n"
    body = "\n".join(ac.llm_str() for ac in analysis.components)
    relations = "\n".join(cr.llm_str() for cr in analysis.components_relations)
    return title + body + relations


class AnalysisInsightsLLM(LLMBaseModel):
    """Project analysis insights as produced by the LLM: components and their relations."""

    description: str = Field(
        description="One paragraph explaining the functionality which is represented by this graph. What the main flow is and what is its purpose."
    )
    components: list[ComponentLLM] = Field(description="List of the components identified in the project.")
    components_relations: list[RelationLLM] = Field(description="List of relations among the components.")

    def llm_str(self):
        return _analysis_str(self)


class ExpandComponent(LLMBaseModel):
    """Decision on whether to expand a component with reasoning."""

    should_expand: bool = Field(description="Whether the component should be expanded in detail or not.")
    reason: str = Field(description="Reasoning behind the decision to expand or not.")

    def llm_str(self):
        return f"- *Should Expand:* {self.should_expand}\n- *Reason:* {self.reason}"


class ValidationInsights(LLMBaseModel):
    """Validation results with status and additional information."""

    is_valid: bool = Field(description="Indicates whether the validation results in valid or not.")
    additional_info: str | None = Field(
        default=None,
        description="Any additional information or context related to the validation.",
    )

    def llm_str(self):
        return f"**Feedback Information:**\n{self.additional_info}"


class UpdateAnalysis(LLMBaseModel):
    """Feedback on how much a diagram needs updating."""

    update_degree: int = Field(
        description="Degree to which the diagram needs update. 0 means no update, 10 means complete update."
    )
    feedback: str = Field(description="Feedback provided on the analysis.")

    def llm_str(self):
        return f"**Feedback:**\n{self.feedback}"


class MetaAnalysisInsights(LLMBaseModel):
    """Insights from analyzing project metadata including type, domain, and architecture."""

    project_type: str = Field(
        description="Type/category of the project (e.g., web framework, data processing, ML library, etc.)"
    )
    domain: str = Field(
        description="Domain or field the project belongs to (e.g., web development, data science, DevOps, etc.)"
    )
    architectural_patterns: list[str] = Field(description="Main architectural patterns typically used in such projects")
    expected_components: list[str] = Field(description="Expected high-level components/modules based on project type")
    technology_stack: list[str] = Field(description="Main technologies, frameworks, and libraries used")
    architectural_bias: str = Field(
        description="Guidance on how to interpret and organize components for this project type"
    )

    def llm_str(self):
        title = "# Project Metadata Analysis\n"
        content = f"""
**Project Type:** {self.project_type}
**Domain:** {self.domain}
**Technology Stack:** {", ".join(self.technology_stack)}
**Architectural Patterns:** {", ".join(self.architectural_patterns)}
**Expected Components:** {", ".join(self.expected_components)}
**Architectural Bias:** {self.architectural_bias}
"""
        return title + content


class FileClassification(LLMBaseModel):
    """Classification of a file to a component."""

    component_name: str = Field(description="Name of the component or module")
    file_path: str = Field(description="Path to the file")

    def llm_str(self):
        return f"`{self.file_path}` -> Component: `{self.component_name}`"


class ComponentFiles(LLMBaseModel):
    """Collection of file classifications for components."""

    file_paths: list[FileClassification] = Field(
        description="All files with their classifications for each of the files assigned to a component."
    )

    def llm_str(self):
        if not self.file_paths:
            return "No files classified."
        title = "# Component File Classifications\n"
        body = "\n".join(f"- `{fc.file_path}` -> Component: `{fc.component_name}`" for fc in self.file_paths)
        return title + body


class ScopeRelations(LLMBaseModel):
    """Relations between components within a single scope."""

    components_relations: list[RelationLLM] = Field(description="Inter-component relationships within this scope.")

    def llm_str(self):
        if not self.components_relations:
            return "No relations found."
        return "\n".join(r.llm_str() for r in self.components_relations)


class FilePath(LLMBaseModel):
    """File path with optional line range reference."""

    file_path: str = Field(description="Full file path for the reference")
    start_line: int | None = Field(
        default=None,
        description="Starting line number in the file for the reference (if applicable).",
    )
    end_line: int | None = Field(
        default=None,
        description="Ending line number in the file for the reference (if applicable).",
    )

    def llm_str(self):
        return f"`{self.file_path}`: ({self.start_line}:{self.end_line})"
