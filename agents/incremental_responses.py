"""Structured LLM responses for one incremental analysis scope."""

from pydantic import Field

from agents.analysis_result_responses import LLMBaseModel, SourceCodeReference


class IncrementalComponentDraft(LLMBaseModel):
    name: str = Field(description="Short name describing only the component's current responsibilities.")
    description: str = Field(description="Current responsibility and main flow of the component.")
    key_entities: list[SourceCodeReference] = Field(
        default_factory=list,
        description="Two to five important symbols selected from this component's assigned clusters.",
    )
    source_cluster_ids: list[str] = Field(
        description="Exact cluster IDs assigned to this component from the mutable cluster set."
    )

    def llm_str(self) -> str:
        return f"{self.name}: {', '.join(self.source_cluster_ids)}"


class IncrementalArchitecturePatch(LLMBaseModel):
    description: str = Field(description="Updated description of this scope's purpose and main flow.")
    components: list[IncrementalComponentDraft] = Field(
        default_factory=list,
        description="Replacement components for the mutable cluster partition only.",
    )

    def llm_str(self) -> str:
        return "\n".join(component.llm_str() for component in self.components) or "No mutable components remain."


class IncrementalRelationDraft(LLMBaseModel):
    src_id: str = Field(description="Source component ID selected from the supplied component IDs.")
    dst_id: str = Field(description="Destination component ID selected from the supplied component IDs.")
    relation: str = Field(description="A concise semantic label for the directed interaction.")
    evidence: str = Field(
        default="",
        description="Concrete reason for a non-static interaction, empty for ordinary static calls.",
    )
    evidence_references: list[SourceCodeReference] = Field(
        default_factory=list,
        description="Real symbols supporting non-static communication; include evidence from both endpoints.",
    )
    key_static_edge_ids: list[str] = Field(
        default_factory=list,
        description="Important edge IDs selected only from the supplied verified static-edge catalog.",
    )

    def llm_str(self) -> str:
        return f"{self.src_id} -{self.relation}-> {self.dst_id}"


class IncrementalRelationDrafts(LLMBaseModel):
    relations: list[IncrementalRelationDraft] = Field(
        default_factory=list,
        description="Relations involving at least one affected component.",
    )

    def llm_str(self) -> str:
        return "\n".join(relation.llm_str() for relation in self.relations) or "No affected relations."
