from dataclasses import dataclass, field
from enum import StrEnum

from pydantic import Field

from agents.agent_responses import LLMBaseModel


@dataclass(frozen=True)
class TraceConfig:
    """Budget constraints for the semantic tracing loop."""

    max_hops: int = 3
    max_fetched_methods: int = 30
    max_parallel_regions: int = 4
    max_neighbor_preview: int = 8


DEFAULT_TRACE_CONFIG = TraceConfig()


class TraceStopReason(StrEnum):
    CONTINUE = "continue"
    NO_MATERIAL_IMPACT = "stop_no_material_semantic_impact"
    CLOSURE_REACHED = "stop_material_semantic_impact_closure_reached"
    FRONTIER_EXHAUSTED = "stop_frontier_exhausted"
    BUDGET_EXHAUSTED = "stop_budget_exhausted"
    UNCERTAIN = "stop_uncertain"


class TraceResponse(LLMBaseModel):
    """LLM response for a single tracing step."""

    status: TraceStopReason = Field(
        description=(
            "continue = more methods to inspect; "
            "stop_no_material_semantic_impact = changes are local with no semantic ripple; "
            "stop_material_semantic_impact_closure_reached = all impacted methods were found; "
            "stop_frontier_exhausted = explored all useful neighbors; "
            "stop_budget_exhausted = more methods would be helpful but the budget is exhausted; "
            "stop_uncertain = cannot determine impact confidently"
        )
    )
    impacted_methods: list[str] = Field(
        default_factory=list,
        description="Qualified names of methods whose semantic description in the diagram is affected by the change.",
    )
    next_methods_to_fetch: list[str] = Field(
        default_factory=list,
        description="Qualified names of methods to inspect in the next step when more context is required.",
    )
    unresolved_frontier: list[str] = Field(
        default_factory=list,
        description="Methods the trace wanted to inspect but could not resolve.",
    )
    reason: str = Field(description="Brief explanation of why this stop or continue decision was made.")
    semantic_impact_summary: str = Field(
        default="",
        description=(
            "One-sentence semantic summary of the impact. Populate only when material impact exists. "
            "Do not mention method names, files, or component names."
        ),
    )
    confidence: float = Field(default=0.8, description="Confidence in this assessment from 0.0 to 1.0.")

    def llm_str(self) -> str:
        parts = [f"Status: {self.status}", f"Reason: {self.reason}"]
        if self.semantic_impact_summary:
            parts.append(f"Semantic impact summary: {self.semantic_impact_summary}")
        if self.impacted_methods:
            parts.append(f"Impacted: {', '.join(self.impacted_methods)}")
        if self.next_methods_to_fetch:
            parts.append(f"Next: {', '.join(self.next_methods_to_fetch)}")
        return "\n".join(parts)


@dataclass
class TraceResult:
    """Output of the full tracing loop."""

    visited_methods: list[str] = field(default_factory=list)
    impacted_methods: list[str] = field(default_factory=list)
    unresolved_frontier: list[str] = field(default_factory=list)
    non_traceable_files: list[str] = field(default_factory=list)
    disconnected_files: list[str] = field(default_factory=list)
    stop_reason: TraceStopReason = TraceStopReason.NO_MATERIAL_IMPACT
    hops_used: int = 0
    semantic_impact_summary: str = ""
