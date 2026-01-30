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


def collect_coupling_values(call_graph: CallGraph) -> tuple[list[float], list[float]]:
    """Collect fan-out and fan-in values for all callable entities.

    Returns:
        A tuple of (fan_out_values, fan_in_values).
    """
    nx_graph = call_graph.to_networkx()
    fan_out_values: list[float] = []
    fan_in_values: list[float] = []

    for node_name in nx_graph.nodes:
        node = call_graph.nodes.get(node_name)
        if node and (node.is_class() or node.is_data()):
            continue
        fan_out_values.append(float(nx_graph.out_degree(node_name)))
        fan_in_values.append(float(nx_graph.in_degree(node_name)))

    return fan_out_values, fan_in_values


def check_fan_out(call_graph: CallGraph, config: HealthCheckConfig) -> StandardCheckSummary:
    """E2: Check efferent coupling (fan-out) per function.

    Fan-out measures how many other functions a given function calls.
    High fan-out indicates a function that does too much or orchestrates
    too many dependencies.
    """
    findings: list[FindingEntity] = []
    total_checked = 0

    threshold = config.get_adaptive_threshold(
        config.fan_out_max,
        config.codebase_stats.fan_out,
        use_adaptive=config.fan_out_percentile is not None,
    )

    for fqn, node in call_graph.nodes.items():
        if node.is_class() or node.is_data():
            continue

        fan_out = len(node.methods_called_by_me)
        total_checked += 1

        if fan_out >= threshold:
            findings.append(
                FindingEntity(
                    entity_name=fqn,
                    file_path=node.file_path,
                    line_start=node.line_start,
                    line_end=node.line_end,
                    metric_value=float(fan_out),
                )
            )

    finding_groups: list[FindingGroup] = []
    if findings:
        finding_groups.append(
            FindingGroup(
                severity=Severity.WARNING,
                threshold=threshold,
                description=f"Functions calling more than {threshold:.1f} other functions",
                entities=sorted(findings, key=lambda e: e.metric_value, reverse=True),
            )
        )

    score = (total_checked - len(findings)) / total_checked if total_checked > 0 else 1.0

    return StandardCheckSummary(
        check_name="fan_out",
        description="Checks efferent coupling: how many other functions each function calls",
        total_entities_checked=total_checked,
        findings_count=len(findings),
        warning_count=len(findings),
        score=score,
        finding_groups=finding_groups,
    )


def check_fan_in(call_graph: CallGraph, config: HealthCheckConfig) -> StandardCheckSummary:
    """E3: Check afferent coupling (fan-in) per function.

    Fan-in measures how many other functions call a given function.
    High fan-in means the function is a critical dependency — changes
    to it are high-risk and affect many callers.
    """
    findings: list[FindingEntity] = []
    total_checked = 0

    threshold = config.get_adaptive_threshold(
        config.fan_in_max,
        config.codebase_stats.fan_in,
        use_adaptive=config.fan_in_percentile is not None,
    )

    nx_graph = call_graph.to_networkx()
    for node_name in nx_graph.nodes:
        node = call_graph.nodes.get(node_name)
        if node and (node.is_class() or node.is_data()):
            continue

        fan_in = nx_graph.in_degree(node_name)
        total_checked += 1

        if fan_in >= threshold:
            findings.append(
                FindingEntity(
                    entity_name=node_name,
                    file_path=node.file_path if node else None,
                    line_start=node.line_start if node else None,
                    line_end=node.line_end if node else None,
                    metric_value=float(fan_in),
                )
            )

    finding_groups: list[FindingGroup] = []
    if findings:
        finding_groups.append(
            FindingGroup(
                severity=Severity.WARNING,
                threshold=threshold,
                description=f"Functions called by more than {threshold:.1f} other functions",
                entities=sorted(findings, key=lambda e: e.metric_value, reverse=True),
            )
        )

    score = (total_checked - len(findings)) / total_checked if total_checked > 0 else 1.0

    return StandardCheckSummary(
        check_name="fan_in",
        description="Checks afferent coupling: how many other functions call each function",
        total_entities_checked=total_checked,
        findings_count=len(findings),
        warning_count=len(findings),
        score=score,
        finding_groups=finding_groups,
    )
