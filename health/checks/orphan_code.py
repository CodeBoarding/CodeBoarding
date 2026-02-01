import logging

import networkx as nx

from health.models import FindingEntity, FindingGroup, Severity, StandardCheckSummary
from static_analyzer.graph import CallGraph

logger = logging.getLogger(__name__)


def check_orphan_code(call_graph: CallGraph) -> StandardCheckSummary:
    """E8: Detect orphan code — functions with no incoming or outgoing calls.

    Orphan nodes are completely disconnected from the call graph. They may
    be dead code, entry points, or utility functions that are only called
    dynamically. Reported as informational findings.
    """
    warning_entities: list[FindingEntity] = []
    nx_graph = call_graph.to_networkx()
    total_nodes = nx_graph.number_of_nodes()

    skipped = 0
    for node_name in nx_graph.nodes:
        node = call_graph.nodes.get(node_name)
        # Skip classes, data entities, and callbacks/anonymous functions —
        # they are not expected to have independent call relationships
        if node and (node.is_class() or node.is_data() or node.is_callback_or_anonymous()):
            skipped += 1
            continue

        in_deg = nx_graph.in_degree(node_name)
        out_deg = nx_graph.out_degree(node_name)

        if in_deg == 0 and out_deg == 0:
            warning_entities.append(
                FindingEntity(
                    entity_name=node_name,
                    file_path=node.file_path if node else None,
                    line_start=node.line_start if node else None,
                    line_end=node.line_end if node else None,
                    metric_value=0.0,
                )
            )

    checked_nodes = total_nodes - skipped
    connected = checked_nodes - len(warning_entities)
    score = connected / checked_nodes if checked_nodes > 0 else 1.0

    finding_groups: list[FindingGroup] = []
    if warning_entities:
        finding_groups.append(
            FindingGroup(
                severity=Severity.WARNING,
                threshold=0,
                description="Functions with no incoming or outgoing calls in the call graph",
                entities=warning_entities,
            )
        )

    return StandardCheckSummary(
        check_name="orphan_code",
        description="Detects functions with no incoming or outgoing calls (potential dead code)",
        total_entities_checked=checked_nodes,
        findings_count=len(warning_entities),
        warning_count=len(warning_entities),
        score=score,
        finding_groups=finding_groups,
    )
