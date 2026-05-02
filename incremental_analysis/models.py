"""Pydantic and dataclass models for trace-based incremental analysis."""

from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from agents.agent_responses import LLMBaseModel


# ---------------------------------------------------------------------------
# Tracing configuration
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class TraceConfig:
    """Budget constraints for the semantic tracing loop."""

    max_hops: int = 3
    max_fetched_methods: int = 30
    max_parallel_regions: int = 4
    max_neighbor_preview: int = 8
    max_cached_turns: int = 4
    llm_max_retries: int = 2
    llm_initial_backoff_s: float = 1.0
    llm_backoff_multiplier: float = 2.0


DEFAULT_TRACE_CONFIG = TraceConfig()


# ---------------------------------------------------------------------------
# Tracing response contract
# ---------------------------------------------------------------------------
class TraceStopReason(StrEnum):
    CONTINUE = "continue"
    NO_MATERIAL_IMPACT = "stop_no_material_semantic_impact"
    CLOSURE_REACHED = "stop_material_semantic_impact_closure_reached"
    FRONTIER_EXHAUSTED = "stop_frontier_exhausted"
    BUDGET_EXHAUSTED = "stop_budget_exhausted"
    UNCERTAIN = "stop_uncertain"
    SYNTAX_ERROR = "stop_syntax_error"
    # System-only (not exposed to the LLM). Set when the planner filters every
    # changed file as cosmetic-only and the LLM is never invoked.
    COSMETIC_ONLY = "stop_cosmetic_only"


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
        description="Qualified names of methods to inspect in the next step (only when status=continue).",
    )
    unresolved_frontier: list[str] = Field(
        default_factory=list,
        description="Methods the trace wanted to inspect but could not resolve.",
    )
    reason: str = Field(
        description="Brief explanation of why this stop/continue decision was made.",
    )
    semantic_impact_summary: str = Field(
        default="",
        description=(
            "One-sentence semantic summary of the impact, only when "
            "status=stop_material_semantic_impact_closure_reached. Leave empty otherwise. "
            "Do not mention method names, files, or component names."
        ),
    )
    confidence: float = Field(
        default=0.8,
        description="Confidence in this assessment (0.0-1.0).",
    )

    def llm_str(self) -> str:
        parts = [f"Status: {self.status}", f"Reason: {self.reason}"]
        if self.semantic_impact_summary:
            parts.append(f"Semantic impact summary: {self.semantic_impact_summary}")
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
    impacted_methods: list[str] = field(default_factory=list)
    visited_methods: list[str] = field(default_factory=list)
    unresolved_frontier: list[str] = field(default_factory=list)
    non_traceable_files: list[str] = field(default_factory=list)
    disconnected_files: list[str] = field(default_factory=list)
    stop_reason: TraceStopReason = TraceStopReason.NO_MATERIAL_IMPACT
    hops_used: int = 0
    semantic_impact_summary: str = ""


# ---------------------------------------------------------------------------
# Escalation level
# ---------------------------------------------------------------------------
class EscalationLevel(StrEnum):
    NONE = "none"
    SCOPED = "scoped"
    ROOT = "root"
    FULL = "full"


class IncrementalSummaryKind(StrEnum):
    NO_CHANGES = "no_changes"
    COSMETIC_ONLY = "cosmetic_only"
    RENAME_ONLY = "rename_only"
    ADDITIVE_ONLY = "additive_only"
    NO_MATERIAL_IMPACT = "no_material_impact"
    MATERIAL_IMPACT = "material_impact"
    SCOPED_REANALYSIS = "scoped_reanalysis"
    FULL_FALLBACK = "full_fallback"
    REQUIRES_FULL_ANALYSIS = "requires_full_analysis"


@dataclass
class IncrementalSummary:
    """Single-sentence summary for an incremental run."""

    kind: IncrementalSummaryKind
    message: str
    used_llm: bool = False
    trace_stop_reason: TraceStopReason | None = None
    escalation_level: EscalationLevel | None = None
    requires_full_analysis: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind.value,
            "message": self.message,
            "usedLlm": self.used_llm,
            "traceStopReason": None if self.trace_stop_reason is None else self.trace_stop_reason.value,
            "escalationLevel": None if self.escalation_level is None else self.escalation_level.value,
            "requiresFullAnalysis": self.requires_full_analysis,
        }


@dataclass
class IncrementalRunResult:
    """Structured return value of ``DiagramGenerator.generate_analysis_incremental()``.

    The wrapper surfaces this back to the IDE as the JSON-RPC response body.
    """

    summary: IncrementalSummary
    trace_result: TraceResult | None = None
    patched_component_ids: list[str] = field(default_factory=list)
    failed_component_ids: list[str] = field(default_factory=list)
    analysis_path: Path | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "summary": self.summary.to_dict(),
            "traceResult": (
                None
                if self.trace_result is None
                else {
                    "stopReason": self.trace_result.stop_reason.value,
                    "hopsUsed": self.trace_result.hops_used,
                    "impactedMethods": list(self.trace_result.impacted_methods),
                    "impactedComponents": [
                        {"componentId": c.component_id, "impactedMethods": list(c.impacted_methods)}
                        for c in self.trace_result.impacted_components
                    ],
                    "unresolvedFrontier": list(self.trace_result.unresolved_frontier),
                    "semanticImpactSummary": self.trace_result.semantic_impact_summary,
                }
            ),
            "patchedComponentIds": list(self.patched_component_ids),
            "failedComponentIds": list(self.failed_component_ids),
            "analysisPath": None if self.analysis_path is None else str(self.analysis_path),
        }


# ---------------------------------------------------------------------------
# JSON Patch models
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
