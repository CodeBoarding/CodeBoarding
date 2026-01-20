from dataclasses import dataclass, field
from typing import Any, Optional
from pydantic import BaseModel, Field


class TokenUsage(BaseModel):
    """Token consumption metrics."""

    total_tokens: int = 0
    input_tokens: int = 0
    output_tokens: int = 0


class ToolUsage(BaseModel):
    """Tool invocation metrics."""

    counts: dict[str, int] = Field(default_factory=dict)
    errors: dict[str, int] = Field(default_factory=dict)


class AgentTokenBreakdown(BaseModel):
    """Per-agent token usage breakdown."""

    input: int = 0
    output: int = 0


# =============================================================================
# End-to-End Evaluation Models
# =============================================================================


class MonitoringMetrics(BaseModel):
    """Aggregated monitoring data across all agents."""

    token_usage: TokenUsage = Field(default_factory=TokenUsage)
    tool_usage: ToolUsage = Field(default_factory=ToolUsage)


class EndToEndMetrics(BaseModel):
    """Metrics returned by EndToEndEval.extract_metrics()."""

    monitoring: MonitoringMetrics = Field(default_factory=MonitoringMetrics)
    code_stats: dict[str, Any] = Field(default_factory=dict)
    mermaid_diagram: str = ""


# =============================================================================
# Scalability Evaluation Models
# =============================================================================


class ScalabilityMetrics(BaseModel):
    """Metrics returned by ScalabilityEval.extract_metrics()."""

    loc: int = 0
    total_tokens: int = 0
    agent_token_usage: dict[str, AgentTokenBreakdown] = Field(default_factory=dict)
    agent_tool_usage: dict[str, dict[str, int]] = Field(default_factory=dict)
    depth: int


# =============================================================================
# Static Analysis Evaluation Models
# =============================================================================


class LanguageSummary(BaseModel):
    """Per-language code statistics."""

    files: int = 0
    loc: int = 0


class StaticAnalysisSummary(BaseModel):
    """Aggregated code statistics."""

    total_files: int = 0
    total_loc: int = 0
    languages: dict[str, LanguageSummary] = Field(default_factory=dict)


class StaticAnalysisMetrics(BaseModel):
    """Metrics returned by StaticAnalysisEval.extract_metrics()."""

    code_stats: StaticAnalysisSummary = Field(default_factory=StaticAnalysisSummary)


# =============================================================================
# Core Pipeline Models (Dataclasses for compatibility with evals/base.py)
# =============================================================================


@dataclass
class ProjectSpec:
    name: str
    url: str
    expected_language: str = ""
    env_vars: dict[str, str] = field(default_factory=dict)
    code_size: str | None = None  # e.g. "small", "medium", "large"
    ground_truth_commit: str | None = None  # Git commit hash the ground truth was labelled for


@dataclass
class RunData:
    run_dir: str
    metadata: dict[str, Any] = field(default_factory=dict)
    code_stats: dict[str, Any] = field(default_factory=dict)
    llm_usage: dict[str, Any] = field(default_factory=dict)


@dataclass
class PipelineResult:
    success: bool
    stderr: str
    pipeline_duration: float
    timestamp: str


@dataclass
class EvalResult:
    project: str
    url: str
    expected_language: str
    success: bool
    duration_seconds: float
    timestamp: str
    metrics: dict[str, Any]
    error: Optional[str] = None
