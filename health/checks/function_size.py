import logging

from health.models import (
    FindingEntity,
    FindingGroup,
    HealthCheckConfig,
    Severity,
    StandardCheckSummary,
)
from static_analyzer.graph import CallGraph

logger = logging.getLogger(__name__)


def collect_function_sizes(call_graph: CallGraph) -> list[float]:
    """Collect function sizes (line counts) for all callable entities in the graph."""
    sizes: list[float] = []
    for node in call_graph.nodes.values():
        if node.is_class() or node.is_data():
            continue
        size = node.line_end - node.line_start
        if size > 0:
            sizes.append(float(size))
    return sizes


def check_function_size(call_graph: CallGraph, config: HealthCheckConfig) -> StandardCheckSummary:
    """E1: Check function/method sizes across the call graph.

    Flags functions that exceed line count thresholds. Large functions are
    harder to understand, test, and maintain.
    """
    findings: list[FindingEntity] = []
    total_checked = 0
    threshold = config.function_size_max

    for fqn, node in call_graph.nodes.items():
        if node.is_class() or node.is_data():
            continue

        size = node.line_end - node.line_start
        if size <= 0:
            continue
        total_checked += 1

        if size >= threshold:
            findings.append(
                FindingEntity(
                    entity_name=fqn,
                    file_path=node.file_path,
                    line_start=node.line_start,
                    line_end=node.line_end,
                    metric_value=size,
                )
            )

    finding_groups: list[FindingGroup] = []
    if findings:
        finding_groups.append(
            FindingGroup(
                severity=Severity.WARNING,
                threshold=threshold,
                description=f"Functions exceeding {threshold:.1f} lines",
                entities=sorted(findings, key=lambda e: e.metric_value, reverse=True),
            )
        )

    score = (total_checked - len(findings)) / total_checked if total_checked > 0 else 1.0

    return StandardCheckSummary(
        check_name="function_size",
        description="Checks that functions/methods do not exceed line count thresholds",
        total_entities_checked=total_checked,
        findings_count=len(findings),
        warning_count=len(findings),
        score=score,
        finding_groups=finding_groups,
    )
