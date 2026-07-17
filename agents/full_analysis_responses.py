"""Structured LLM responses used only by full and details analysis agents."""

from pydantic import Field

from agents.analysis_result_responses import (
    AnalysisInsights,
    Component,
    LLMBaseModel,
    Relation,
    SourceCodeReference,
)
from agents.cluster_ids import GraphClusterId


class ClustersComponent(LLMBaseModel):
    name: str = Field(description="Short, descriptive name for this cluster group.")
    cluster_ids: list[GraphClusterId] = Field(description="CFG cluster IDs grouped into this component.")
    description: str = Field(description="Purpose, grouping rationale, interactions, and important symbols.")

    def llm_str(self) -> str:
        ids_str = ", ".join(str(cluster_id) for cluster_id in self.cluster_ids)
        return f"**{self.name}** (cluster_ids: [{ids_str}])\n   {self.description}"


class ClusterAnalysis(LLMBaseModel):
    cluster_components: list[ClustersComponent] = Field(description="Logical component groupings of CFG clusters.")

    def llm_str(self) -> str:
        if not self.cluster_components:
            return "No clusters analyzed."
        return "# Grouped Cluster Components\n" + "\n".join(
            component.llm_str() for component in self.cluster_components
        )


class ComponentArchitecture(LLMBaseModel):
    description: str = Field(description="Purpose and main flow represented by this component scope.")
    components: list[Component] = Field(description="Components identified in the scope.")

    def llm_str(self) -> str:
        if not self.components:
            return "No abstract components found."
        return "# Abstract Components Overview\n" + "\n".join(component.llm_str() for component in self.components)


class ComponentApiSurface(LLMBaseModel):
    component_name: str = Field(description="Exact component name this API surface describes.")
    provided_interfaces: list[SourceCodeReference] = Field(
        default_factory=list,
        description="Methods, classes, or config symbols exposed as entrypoints.",
    )
    consumed_interfaces: list[SourceCodeReference] = Field(
        default_factory=list,
        description="Methods, classes, or config symbols consumed from other components.",
    )
    incoming_api_paths: list[str] = Field(default_factory=list, description="Ways other components enter this API.")
    outgoing_api_paths: list[str] = Field(default_factory=list, description="Ways this component reaches other APIs.")
    notes: str = Field(default="", description="Short notes about the component's API role.")

    def llm_str(self) -> str:
        provided = ", ".join(reference.llm_str() for reference in self.provided_interfaces) or "none"
        consumed = ", ".join(reference.llm_str() for reference in self.consumed_interfaces) or "none"
        incoming = ", ".join(self.incoming_api_paths) or "none"
        outgoing = ", ".join(self.outgoing_api_paths) or "none"
        return (
            f"**{self.component_name}**\n"
            f"  Provided: {provided}\n"
            f"  Consumed: {consumed}\n"
            f"  Incoming API paths: {incoming}\n"
            f"  Outgoing API paths: {outgoing}\n"
            f"  Notes: {self.notes}"
        )


class ComponentApiSurfaces(LLMBaseModel):
    api_surfaces: list[ComponentApiSurface] = Field(description="API surface for each component in this scope.")

    def llm_str(self) -> str:
        if not self.api_surfaces:
            return "No component API surfaces found."
        return "\n".join(surface.llm_str() for surface in self.api_surfaces)


class ComponentRelations(LLMBaseModel):
    components_relations: list[Relation] = Field(description="Relations among the components.")

    def llm_str(self) -> str:
        if not self.components_relations:
            return "No component relations found."
        return "\n".join(relation.llm_str() for relation in self.components_relations)


class CFGComponent(LLMBaseModel):
    name: str = Field(description="Name of the abstract component")
    description: str = Field(description="One paragraph explaining the component.")
    referenced_source: list[str] = Field(description="Qualified names within this component.")

    def llm_str(self) -> str:
        related = ", ".join(f"`{name}`" for name in self.referenced_source)
        return (
            f"**Component:** `{self.name}`\n   - *Description*: {self.description}\n   - *Related*: {related}".strip()
        )


class CFGAnalysisInsights(LLMBaseModel):
    components: list[CFGComponent] = Field(description="Components identified in the CFG.")
    components_relations: list[Relation] = Field(description="Relations among the CFG components.")

    def llm_str(self) -> str:
        if not self.components:
            return "No abstract components found in the CFG."
        return "# Abstract Components Overview from CFG\n" + "\n".join(
            component.llm_str() for component in self.components
        )


class ExpandComponent(LLMBaseModel):
    should_expand: bool = Field(description="Whether the component should be expanded in detail.")
    reason: str = Field(description="Reasoning behind the decision.")

    def llm_str(self) -> str:
        return f"- *Should Expand:* {self.should_expand}\n- *Reason:* {self.reason}"


class ValidationInsights(LLMBaseModel):
    is_valid: bool = Field(description="Whether the analysis is valid.")
    additional_info: str | None = Field(default=None, description="Additional validation context.")

    def llm_str(self) -> str:
        return f"**Feedback Information:**\n{self.additional_info}"


class UpdateAnalysis(LLMBaseModel):
    update_degree: int = Field(description="Required update degree from 0 to 10.")
    feedback: str = Field(description="Feedback on the analysis.")

    def llm_str(self) -> str:
        return f"**Feedback:**\n{self.feedback}"


class MetaAnalysisInsights(LLMBaseModel):
    project_type: str = Field(description="Project type or category.")
    domain: str = Field(description="Project domain.")
    architectural_patterns: list[str] = Field(description="Likely architectural patterns.")
    expected_components: list[str] = Field(description="Expected high-level components.")
    technology_stack: list[str] = Field(description="Technologies, frameworks, and libraries.")
    architectural_bias: str = Field(description="Guidance for interpreting component boundaries.")

    def llm_str(self) -> str:
        return f"""# Project Metadata Analysis
**Project Type:** {self.project_type}
**Domain:** {self.domain}
**Technology Stack:** {", ".join(self.technology_stack)}
**Architectural Patterns:** {", ".join(self.architectural_patterns)}
**Expected Components:** {", ".join(self.expected_components)}
**Architectural Bias:** {self.architectural_bias}
"""


class FileClassification(LLMBaseModel):
    component_name: str = Field(description="Name of the component or module")
    file_path: str = Field(description="Path to the file")

    def llm_str(self) -> str:
        return f"`{self.file_path}` -> Component: `{self.component_name}`"


class ComponentFiles(LLMBaseModel):
    file_paths: list[FileClassification] = Field(description="Files and their component classifications.")

    def llm_str(self) -> str:
        if not self.file_paths:
            return "No files classified."
        return "# Component File Classifications\n" + "\n".join(
            f"- `{classification.file_path}` -> Component: `{classification.component_name}`"
            for classification in self.file_paths
        )


class FilePath(LLMBaseModel):
    file_path: str = Field(description="Full file path for the reference")
    start_line: int | None = Field(default=None, description="Optional starting line.")
    end_line: int | None = Field(default=None, description="Optional ending line.")

    def llm_str(self) -> str:
        return f"`{self.file_path}`: ({self.start_line}:{self.end_line})"
