import logging
from collections import defaultdict

from health.models import (
    FindingEntity,
    FindingGroup,
    HealthCheckConfig,
    Severity,
    StandardCheckSummary,
)
from static_analyzer.graph import CallGraph

logger = logging.getLogger(__name__)


def _group_methods_by_class(call_graph: CallGraph) -> dict[str, list[str]]:
    """Group node FQNs by their class prefix."""
    class_methods: dict[str, list[str]] = defaultdict(list)
    delimiter = call_graph.delimiter
    for fqn in call_graph.nodes:
        parts = fqn.rsplit(delimiter, 1)
        if len(parts) == 2:
            class_methods[parts[0]].append(fqn)
    return class_methods


def collect_god_class_values(
    call_graph: CallGraph,
) -> tuple[list[float], list[float], list[float]]:
    """Collect per-class method counts, LOC estimates, and fan-out totals.

    Returns:
        A tuple of (method_counts, class_loc_values, class_fan_out_values).
    """
    class_methods = _group_methods_by_class(call_graph)
    method_counts: list[float] = []
    class_loc_values: list[float] = []
    class_fan_out_values: list[float] = []

    for _class_name, method_fqns in class_methods.items():
        if len(method_fqns) < 2:
            continue

        method_counts.append(float(len(method_fqns)))

        total_fan_out = sum(
            len(call_graph.nodes[fqn].methods_called_by_me) for fqn in method_fqns if fqn in call_graph.nodes
        )
        class_fan_out_values.append(float(total_fan_out))

        min_line = float("inf")
        max_line = 0
        for fqn in method_fqns:
            node = call_graph.nodes.get(fqn)
            if node:
                min_line = min(min_line, node.line_start)
                max_line = max(max_line, node.line_end)
        if max_line > min_line:
            class_loc_values.append(float(max_line - int(min_line)))

    return method_counts, class_loc_values, class_fan_out_values


def check_god_classes(call_graph: CallGraph, hierarchy: dict | None, config: HealthCheckConfig) -> StandardCheckSummary:
    """E4: Detect god classes — classes with too many methods, too much code, or too many dependencies.

    A god class violates the Single Responsibility Principle by doing too much.
    Detection criteria (any one triggers a finding):
    - Too many methods (> threshold)
    - Too many lines of code (> threshold)
    - Too high aggregate fan-out (> threshold)
    """
    findings: list[FindingEntity] = []

    class_methods = _group_methods_by_class(call_graph)

    total_checked = 0
    for class_name, method_fqns in class_methods.items():
        if len(method_fqns) < 2:
            continue

        # Only flag actual classes, not module-level groupings
        parent_node = call_graph.nodes.get(class_name)
        is_real_class = (parent_node is not None and parent_node.is_class()) or (
            hierarchy is not None and class_name in hierarchy
        )
        if not is_real_class:
            continue

        total_checked += 1

        method_count = len(method_fqns)
        total_fan_out = sum(
            len(call_graph.nodes[fqn].methods_called_by_me) for fqn in method_fqns if fqn in call_graph.nodes
        )

        # Get class LOC from hierarchy if available, else estimate from method spans
        class_loc = 0
        class_file = None
        if hierarchy and class_name in hierarchy:
            h = hierarchy[class_name]
            class_loc = h.get("line_end", 0) - h.get("line_start", 0)
            class_file = h.get("file_path")
        else:
            min_line = float("inf")
            max_line = 0
            for fqn in method_fqns:
                node = call_graph.nodes.get(fqn)
                if node:
                    min_line = min(min_line, node.line_start)
                    max_line = max(max_line, node.line_end)
                    if class_file is None:
                        class_file = node.file_path
            if max_line > min_line:
                class_loc = max_line - int(min_line)

        max_metric = 0.0
        is_god_class = False

        if method_count >= config.god_class_method_count_max:
            max_metric = max(max_metric, float(method_count))
            is_god_class = True
        if class_loc >= config.god_class_loc_max:
            max_metric = max(max_metric, float(class_loc))
            is_god_class = True
        if total_fan_out >= config.god_class_fan_out_max:
            max_metric = max(max_metric, float(total_fan_out))
            is_god_class = True

        if not is_god_class:
            continue

        findings.append(
            FindingEntity(
                entity_name=class_name,
                file_path=class_file,
                line_start=None,
                line_end=None,
                metric_value=max_metric,
            )
        )

    finding_groups: list[FindingGroup] = []
    if findings:
        finding_groups.append(
            FindingGroup(
                severity=Severity.WARNING,
                threshold=config.god_class_method_count_max,
                description="Classes exceeding god class criteria (methods, LOC, or fan-out)",
                entities=sorted(findings, key=lambda e: e.metric_value, reverse=True),
            )
        )

    score = (total_checked - len(findings)) / total_checked if total_checked > 0 else 1.0

    return StandardCheckSummary(
        check_name="god_class",
        description="Detects classes with too many methods, too much code, or too many outgoing dependencies",
        total_entities_checked=total_checked,
        findings_count=len(findings),
        warning_count=len(findings),
        score=score,
        finding_groups=finding_groups,
    )
