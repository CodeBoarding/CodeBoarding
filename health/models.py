import logging
import math
from typing import Annotated, Literal

from pydantic import BaseModel, Discriminator, Field

logger = logging.getLogger(__name__)


class DistributionStats(BaseModel):
    """Statistical summary of a single metric distribution.

    Used for adaptive threshold computation across all health checks.
    """

    mean: float = 0.0
    std: float = 0.0
    max_value: float = 0.0
    p99: float = 0.0

    @staticmethod
    def from_values(values: list[float]) -> "DistributionStats | None":
        """Compute distribution statistics from a list of values.

        Returns None if the list is empty.
        """
        if not values:
            return None

        n = len(values)
        mean = sum(values) / n
        max_value = max(values)
        std = 0.0
        if n > 1:
            variance = sum((x - mean) ** 2 for x in values) / n
            std = math.sqrt(variance)

        sorted_values = sorted(values)
        p99 = _compute_percentile(sorted_values, 0.99)

        return DistributionStats(mean=mean, std=std, max_value=max_value, p99=p99)


def _compute_percentile(sorted_values: list[float], percentile: float) -> float:
    """Compute the percentile value from a sorted list using linear interpolation."""
    if not sorted_values:
        return 0.0
    n = len(sorted_values)
    if n == 1:
        return float(sorted_values[0])

    index = (n - 1) * percentile
    lower = int(index)
    upper = min(lower + 1, n - 1)
    fraction = index - lower

    return sorted_values[lower] + fraction * (sorted_values[upper] - sorted_values[lower])


class Severity:
    """Severity level constants for health findings."""

    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


class FindingEntity(BaseModel):
    """A single entity flagged by a health check.

    Note: file_path, line_start, and line_end are optional and may be None
    for checks where location information is not applicable (e.g., circular
    dependencies between packages, package instability metrics).
    """

    entity_name: str = Field(description="Fully qualified name of the affected entity")
    file_path: str | None = Field(
        default=None,
        description="File path of the affected entity (None if not applicable)",
    )
    line_start: int | None = Field(
        default=None,
        description="Start line of the affected entity (None if not applicable)",
    )
    line_end: int | None = Field(
        default=None,
        description="End line of the affected entity (None if not applicable)",
    )
    metric_value: float = Field(description="The measured metric value")


class FindingGroup(BaseModel):
    """A group of findings at the same severity level within a health check."""

    severity: str = Field(description="Severity level: info, warning, critical")
    threshold: float = Field(description="The threshold that was exceeded for this severity level")
    description: str = Field(description="Human-readable description of what this severity group means")
    entities: list[FindingEntity] = Field(default_factory=list)


class BaseCheckSummary(BaseModel):
    """Base class for all health check summaries."""

    check_name: str = Field(description="Name of the health check")
    description: str = Field(description="What this check measures")
    language: str | None = Field(
        default=None,
        description="Programming language this check applies to (e.g. 'python', 'typescript'). "
        "Set when the repository contains multiple languages.",
    )


class StandardCheckSummary(BaseCheckSummary):
    """Standard check summary with entity-level findings."""

    check_type: Literal["standard"] = "standard"
    total_entities_checked: int = Field(description="Number of entities evaluated")
    findings_count: int = Field(description="Number of findings (threshold violations)")
    warning_count: int = Field(default=0)
    score: float = Field(
        description="Health score from 0.0 (poor) to 1.0 (healthy). Calculated as: (entities_checked - findings_count) / entities_checked. A score of 1.0 means all entities passed the check."
    )
    finding_groups: list[FindingGroup] = Field(default_factory=list)

    @property
    def findings(self) -> list[FindingEntity]:
        """Flatten all findings from all finding groups into a single list.

        This property provides backwards compatibility for code that expects
        a flat list of findings rather than grouped findings.
        """
        result: list[FindingEntity] = []
        for group in self.finding_groups:
            result.extend(group.entities)
        return result


class CircularDependencyCheck(BaseCheckSummary):
    """Specialized check for package-level circular dependencies."""

    check_type: Literal["circular_dependencies"] = "circular_dependencies"
    cycles: list[str] = Field(default_factory=list, description="List of circular dependency cycles")
    packages_checked: int = Field(description="Total number of packages analyzed")
    packages_in_cycles: int = Field(description="Number of packages involved in cycles")

    @property
    def score(self) -> float:
        """Computed score based on clean packages ratio."""
        return (
            (self.packages_checked - self.packages_in_cycles) / self.packages_checked
            if self.packages_checked > 0
            else 1.0
        )


# Type alias for backwards compatibility
CheckSummary = StandardCheckSummary | CircularDependencyCheck


class FileHealthSummary(BaseModel):
    """Aggregated health metrics for a single file."""

    file_path: str
    total_findings: int = 0
    warning_findings: int = 0
    composite_risk_score: float = Field(
        default=0.0,
        description="Composite risk score (0-100) combining all check results for this file",
    )


class HealthReport(BaseModel):
    """Complete health report for a repository."""

    repository_name: str
    timestamp: str = Field(description="ISO format timestamp of when the report was generated")
    overall_score: float = Field(
        description="Overall health score from 0.0 (poor) to 1.0 (healthy). Calculated as a weighted average of individual check scores, weighted by entities_checked per check."
    )
    check_summaries: list[Annotated[StandardCheckSummary | CircularDependencyCheck, Discriminator("check_type")]] = (
        Field(default_factory=list)
    )
    file_summaries: list[FileHealthSummary] = Field(
        default_factory=list, description="Top 20 highest-risk files by composite score"
    )


class CodebaseStats(BaseModel):
    """Statistical metrics about the codebase for adaptive thresholds.

    Each field is a DistributionStats for a specific metric, or None if the
    metric could not be computed (e.g., no functions, no classes, no hierarchy).
    """

    function_size: DistributionStats | None = None
    fan_out: DistributionStats | None = None
    fan_in: DistributionStats | None = None
    class_method_count: DistributionStats | None = None
    class_loc: DistributionStats | None = None
    class_fan_out: DistributionStats | None = None
    inheritance_depth: DistributionStats | None = None


class HealthCheckConfig(BaseModel):
    """Configuration thresholds for health checks.

    Each metric has a single warning threshold that can be either:
    - Fixed: an absolute value (e.g., function_size_max = 500)
    - Adaptive: percentile of the codebase distribution (e.g., function_size_percentile = 0.999)

    When adaptive thresholds are set (non-None), they take precedence over fixed thresholds.
    The fixed value acts as a conservative upper-bound cap.
    """

    # Statistical baseline for adaptive thresholds (computed from codebase)
    codebase_stats: CodebaseStats = Field(default_factory=CodebaseStats)

    # E1: Function size (lines)
    function_size_max: int = 500
    function_size_percentile: float | None = 0.999  # 99.9th percentile

    def get_adaptive_threshold(
        self, max_value: int, stats: DistributionStats | None, use_adaptive: bool = True
    ) -> float:
        """Get adaptive threshold with p99 cap.

        When stats are available and use_adaptive is True, uses min(p99, max_value).
        Otherwise falls back to the fixed max_value.

        Args:
            max_value: The fixed maximum value (conservative upper bound)
            stats: The distribution stats for this metric (if available)
            use_adaptive: Whether to use adaptive thresholding (default True)

        Returns:
            The computed threshold (float)
        """
        if use_adaptive and stats is not None:
            return min(stats.p99, float(max_value))
        return float(max_value)

    # E2: Fan-out (outgoing calls)
    fan_out_max: int = 40
    fan_out_percentile: float | None = 0.999

    # E3: Fan-in (incoming calls)
    fan_in_max: int = 60
    fan_in_percentile: float | None = 0.999

    # E4: God class
    god_class_method_count_max: int = 30
    god_class_loc_max: int = 800
    god_class_fan_out_max: int = 50
    god_class_method_count_percentile: float | None = 0.997  # 99.7th percentile
    god_class_loc_percentile: float | None = 0.997
    god_class_fan_out_percentile: float | None = 0.997

    # E5: Inheritance depth
    inheritance_depth_max: int = 5

    # E6: Package-level cycle detection via nx.simple_cycles()
    max_cycles_reported: int = 50

    # E8: Disconnected nodes (potential dead code)
    # no configurable threshold

    # E9: Package instability
    instability_high: float = 0.8

    # E10: Component cohesion (low cohesion threshold)
    cohesion_low: float = 0.1
