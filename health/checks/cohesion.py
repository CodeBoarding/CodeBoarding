import logging

from health.models import FindingEntity, FindingGroup, HealthCheckConfig, Severity, StandardCheckSummary
from static_analyzer.graph import CallGraph

logger = logging.getLogger(__name__)


def check_component_cohesion(call_graph: CallGraph, config: HealthCheckConfig) -> StandardCheckSummary:
    """E10: Measure component cohesion via internal vs external edge ratio per cluster.

    For each cluster identified by the call graph clustering, compute:
        cohesion = internal_edges / total_edges

    Low cohesion means the cluster's nodes talk more to nodes outside the
    cluster than inside it, suggesting the grouping may not reflect
    actual code organization.
    """
    warning_entities: list[FindingEntity] = []

    cluster_result = call_graph.cluster()
    if not cluster_result.clusters:
        return StandardCheckSummary(
            check_name="component_cohesion",
            description="Measures internal vs external edge ratio per component/cluster",
            total_entities_checked=0,
            findings_count=0,
            score=1.0,
            finding_groups=[],
        )

    total_checked = 0

    for cluster_id, node_names in cluster_result.clusters.items():
        internal_edges = 0
        external_edges = 0

        for node_name in node_names:
            node = call_graph.nodes.get(node_name)
            if not node:
                continue
            for called_fqn in node.methods_called_by_me:
                if called_fqn in node_names:
                    internal_edges += 1
                else:
                    external_edges += 1

        total_edges = internal_edges + external_edges
        if total_edges == 0:
            continue

        total_checked += 1
        cohesion = internal_edges / total_edges

        # Get representative file for the cluster
        cluster_files = cluster_result.get_files_for_cluster(cluster_id)
        representative_file = next(iter(cluster_files), None) if cluster_files else None

        if cohesion <= config.cohesion_low:
            warning_entities.append(
                FindingEntity(
                    entity_name=f"cluster_{cluster_id}",
                    file_path=representative_file,
                    line_start=None,
                    line_end=None,
                    metric_value=round(cohesion, 3),
                )
            )

    finding_groups: list[FindingGroup] = []
    if warning_entities:
        finding_groups.append(
            FindingGroup(
                severity=Severity.WARNING,
                threshold=config.cohesion_low,
                description=f"Components with low cohesion (below {config.cohesion_low})",
                entities=sorted(warning_entities, key=lambda e: e.metric_value),
            )
        )

    total_findings = len(warning_entities)
    passing = total_checked - total_findings
    score = passing / total_checked if total_checked > 0 else 1.0

    return StandardCheckSummary(
        check_name="component_cohesion",
        description="Measures internal vs external edge ratio per component/cluster",
        total_entities_checked=total_checked,
        findings_count=total_findings,
        warning_count=len(warning_entities),
        score=score,
        finding_groups=finding_groups,
    )
