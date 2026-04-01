"""Pydantic models for trace-based incremental analysis."""

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field

from agents.agent_responses import LLMBaseModel


# ---------------------------------------------------------------------------
# Tracing configuration
# ---------------------------------------------------------------------------
@dataclass
class TraceConfig:
    """Budget constraints for the semantic tracing loop."""

    max_hops: int = 3
    max_fetched_methods: int = 30
    max_tokens: int = 100_000


# ---------------------------------------------------------------------------
# Tracing response contract (step 8)
# ---------------------------------------------------------------------------
class TraceStopReason(StrEnum):
    CONTINUE = "continue"
    NO_MATERIAL_IMPACT = "stop_no_material_semantic_impact"
    CLOSURE_REACHED = "stop_material_semantic_impact_closure_reached"
    UNCERTAIN = "stop_uncertain"


class TraceResponse(LLMBaseModel):
    """LLM response for a single tracing step."""

    status: TraceStopReason = Field(
        description=(
            "continue = more methods to inspect; "
            "stop_no_material_semantic_impact = changes are local with no semantic ripple; "
            "stop_material_semantic_impact_closure_reached = all impacted methods found; "
            "stop_uncertain = cannot determine impact confidently"
        )
    )
    impacted_methods: list[str] = Field(
        default_factory=list,
        description="Qualified names of methods whose semantic description in the diagram is affected by the change.",
    )
    next_methods_to_fetch: list[str] = Field(
        default_factory=list,
        description="Qualified names of methods to inspect in the next step (only when status=continue).",
    )
    unresolved_frontier: list[str] = Field(
        default_factory=list,
        description="Methods the trace wanted to inspect but could not resolve.",
    )
    reason: str = Field(
        description="Brief explanation of why this stop/continue decision was made.",
    )
    confidence: float = Field(
        default=0.8,
        description="Confidence in this assessment (0.0-1.0).",
    )

    def llm_str(self) -> str:
        parts = [f"Status: {self.status}", f"Reason: {self.reason}"]
        if self.impacted_methods:
            parts.append(f"Impacted: {', '.join(self.impacted_methods)}")
        if self.next_methods_to_fetch:
            parts.append(f"Next: {', '.join(self.next_methods_to_fetch)}")
        return "\n".join(parts)


# ---------------------------------------------------------------------------
# Trace result (post-tracing output)
# ---------------------------------------------------------------------------
@dataclass
class ImpactedComponent:
    """A component whose diagram content needs patching."""

    component_id: str
    impacted_methods: list[str] = field(default_factory=list)


@dataclass
class TraceResult:
    """Output of the full tracing loop."""

    impacted_components: list[ImpactedComponent] = field(default_factory=list)
    all_impacted_methods: list[str] = field(default_factory=list)
    unresolved_frontier: list[str] = field(default_factory=list)
    stop_reason: TraceStopReason = TraceStopReason.NO_MATERIAL_IMPACT
    tokens_consumed: int = 0


# ---------------------------------------------------------------------------
# JSON Patch models (step 10)
# ---------------------------------------------------------------------------
class JsonPatchOp(BaseModel):
    """A single RFC 6902 JSON Patch operation."""

    op: Literal["add", "remove", "replace"] = Field(description="Patch operation type.")
    path: str = Field(description="JSON Pointer path to the target location.")
    value: Any = Field(default=None, description="Value for add/replace operations.")


class AnalysisPatch(LLMBaseModel):
    """LLM-generated patch for a sub-analysis."""

    sub_analysis_id: str = Field(description="The component_id of the parent sub-analysis being patched.")
    reasoning: str = Field(description="Brief explanation of what changed and why the patch is needed.")
    patches: list[JsonPatchOp] = Field(
        description="RFC 6902 JSON Patch operations against the EASE-encoded sub-analysis."
    )

    def llm_str(self) -> str:
        ops = "; ".join(f"{p.op} {p.path}" for p in self.patches)
        return f"Patch {self.sub_analysis_id}: {self.reasoning} [{ops}]"


# ---------------------------------------------------------------------------
# Escalation level (step 12)
# ---------------------------------------------------------------------------
class EscalationLevel(StrEnum):
    NONE = "none"
    SCOPED = "scoped"
    ROOT = "root"
    FULL = "full"
