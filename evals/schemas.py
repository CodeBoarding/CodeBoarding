from typing import Any
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
