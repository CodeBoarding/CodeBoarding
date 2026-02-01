import logging
from typing import Annotated, Literal

from pydantic import BaseModel, Discriminator, Field

logger = logging.getLogger(__name__)


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
    metric_value: int | float = Field(description="The measured metric value")


class FindingGroup(BaseModel):
    """A group of findings at the same severity level within a health check."""

    severity: str = Field(description="Severity level: info, warning, critical")
    threshold: int | float = Field(description="The threshold that was exceeded for this severity level")
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


class HealthCheckConfig(BaseModel):
    """Configuration thresholds for health checks."""

    # E1: Function size (lines)
    function_size_max: int = 150

    # E2: Fan-out (outgoing calls)
    fan_out_max: int = 10

    # E3: Fan-in (incoming calls)
    fan_in_max: int = 10

    # E4: God class
    god_class_method_count_max: int = 25
    god_class_loc_max: int = 400
    god_class_fan_out_max: int = 30

    # E5: Inheritance depth
    inheritance_depth_max: int = 5

    # E6: Package-level cycle detection via nx.simple_cycles()
    max_cycles_reported: int = 50

    # E9: Package instability
    instability_high: float = 0.8

    # E10: Component cohesion (low cohesion threshold)
    cohesion_low: float = 0.1
