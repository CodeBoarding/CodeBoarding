"""Structured relation responses for incremental analysis."""

from pydantic import Field

from agents.analysis_result_responses import LLMBaseModel, SourceCodeReference


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
        description="Real symbols supporting non-static communication from both endpoints.",
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
