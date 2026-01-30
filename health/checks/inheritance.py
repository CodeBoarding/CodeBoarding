import logging
from collections import defaultdict

from health.models import (
    FindingEntity,
    FindingGroup,
    HealthCheckConfig,
    Severity,
    StandardCheckSummary,
)

logger = logging.getLogger(__name__)


def _compute_inheritance_depths(hierarchy: dict) -> dict[str, int]:
    """Compute inheritance depth for all classes using iterative BFS.

    Returns a mapping of class name to its depth in the hierarchy.
    """
    depth_cache: dict[str, int] = {}
    children: dict[str, list[str]] = defaultdict(list)

    for class_name, info in hierarchy.items():
        for superclass in info.get("superclasses", []):
            children[superclass].append(class_name)

    # BFS from root classes (no superclasses)
    roots = [name for name, info in hierarchy.items() if not info.get("superclasses")]
    queue = [(root, 0) for root in roots]
    while queue:
        current, depth = queue.pop(0)
        if current in depth_cache and depth <= depth_cache[current]:
            continue
        depth_cache[current] = depth
        for child in children.get(current, []):
            queue.append((child, depth + 1))

    # Handle classes not reachable from roots (external superclasses not in hierarchy)
    for class_name, info in hierarchy.items():
        if class_name not in depth_cache:
            supers = info.get("superclasses", [])
            depth = 0
            for s in supers:
                if s in depth_cache:
                    depth = max(depth, depth_cache[s] + 1)
                else:
                    depth = max(depth, 1)
            depth_cache[class_name] = depth

    return depth_cache


def collect_inheritance_depths(hierarchy: dict) -> list[float]:
    """Collect inheritance depth values for all classes in the hierarchy."""
    depth_cache = _compute_inheritance_depths(hierarchy)
    return [float(d) for d in depth_cache.values()]


def check_inheritance_depth(hierarchy: dict, config: HealthCheckConfig) -> StandardCheckSummary:
    """E5: Check inheritance depth for all classes.

    Deep inheritance hierarchies are fragile and hard to understand.
    Each additional level adds complexity and makes changes riskier.
    """
    findings: list[FindingEntity] = []
    total_checked = 0

    threshold = config.get_adaptive_threshold(
        config.inheritance_depth_max,
        config.codebase_stats.inheritance_depth,
        use_adaptive=config.inheritance_depth_percentile is not None,
    )

    depth_cache = _compute_inheritance_depths(hierarchy)

    for class_name, depth in depth_cache.items():
        if class_name not in hierarchy:
            continue
        total_checked += 1
        info = hierarchy[class_name]

        if depth >= threshold:
            findings.append(
                FindingEntity(
                    entity_name=class_name,
                    file_path=info.get("file_path"),
                    line_start=info.get("line_start"),
                    line_end=info.get("line_end"),
                    metric_value=float(depth),
                )
            )

    finding_groups: list[FindingGroup] = []
    if findings:
        finding_groups.append(
            FindingGroup(
                severity=Severity.WARNING,
                threshold=threshold,
                description=f"Classes with inheritance depth exceeding {threshold:.1f}",
                entities=sorted(findings, key=lambda e: e.metric_value, reverse=True),
            )
        )

    score = (total_checked - len(findings)) / total_checked if total_checked > 0 else 1.0

    return StandardCheckSummary(
        check_name="inheritance_depth",
        description="Checks that class inheritance hierarchies do not exceed depth thresholds",
        total_entities_checked=total_checked,
        findings_count=len(findings),
        warning_count=len(findings),
        score=score,
        finding_groups=finding_groups,
    )
