import logging
from enum import Enum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Discriminator, Field

logger = logging.getLogger(__name__)


class Severity(str, Enum):
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

    severity: Severity = Field(description="Severity level: info, warning, critical")
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

    model_config = ConfigDict(extra="ignore")

    function_size_max: int = Field(
        default=150,
        description="Maximum lines of code allowed in a single function before it is flagged. Range: 50-500. Default: 150.",
    )
    fan_out_max: int = Field(
        default=10,
        description="Maximum number of outgoing calls from a function. High fan-out indicates a function depends on too many others. Range: 5-30. Default: 10.",
    )
    fan_in_max: int = Field(
        default=10,
        description="Maximum number of incoming calls to a function. High fan-in means many functions depend on this one, making changes risky. Range: 5-50. Default: 10.",
    )
    god_class_method_count_max: int = Field(
        default=25,
        description="Maximum number of methods a class can have before being flagged as a God Class. Range: 10-50. Default: 25.",
    )
    god_class_loc_max: int = Field(
        default=400,
        description="Maximum lines of code in a class before being flagged as a God Class. Range: 200-1000. Default: 400.",
    )
    god_class_fan_out_max: int = Field(
        default=30,
        description="Maximum outgoing dependencies from a class before being flagged as a God Class. Range: 10-60. Default: 30.",
    )
    inheritance_depth_max: int = Field(
        default=5,
        description="Maximum depth of class inheritance hierarchy. Deep hierarchies are harder to understand. Range: 3-10. Default: 5.",
    )
    max_cycles_reported: int = Field(
        default=50,
        description="Maximum number of circular dependency cycles to include in the report. Range: 10-200. Default: 50.",
    )
    orphan_exclude_patterns: list[str] = Field(
        default_factory=list,
        description="Glob patterns for entities excluded from orphan code detection. Managed via .healthignore file.",
    )
    instability_high: float = Field(
        default=0.8,
        description="Package instability threshold (0.0 = fully stable, 1.0 = fully unstable). Packages above this value are flagged. Range: 0.5-1.0. Default: 0.8.",
    )
    cohesion_low: float = Field(
        default=0.1,
        description="Low cohesion threshold for components. Components below this value are flagged as having poor internal cohesion. Range: 0.0-0.5. Default: 0.1.",
    )
