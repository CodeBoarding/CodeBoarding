import logging
import os

from health.models import FindingEntity, FindingGroup, Severity, StandardCheckSummary
from static_analyzer.graph import CallGraph

logger = logging.getLogger(__name__)


def _is_dunder_method(node_name: str, delimiter: str) -> bool:
    """Check if the node represents a Python dunder method (e.g. __init__, __getattr__).

    Dunder methods are invoked implicitly by the Python runtime and rarely
    appear as explicit edges in the call graph.
    """
    short_name = node_name.rsplit(delimiter, 1)[-1]
    return short_name.startswith("__") and short_name.endswith("__")


def _is_init_module_function(file_path: str | None) -> bool:
    """Check if the node is defined in an __init__.py file.

    Functions in __init__.py are typically re-exports or package-level
    utilities whose calls are not reliably captured by LSP call hierarchy.
    """
    if not file_path:
        return False
    return os.path.basename(file_path) == "__init__.py"


def check_orphan_code(call_graph: CallGraph) -> StandardCheckSummary:
    """E8: Detect orphan code — functions with no incoming or outgoing calls.

    Orphan nodes are completely disconnected from the call graph. They may
    be dead code, entry points, or utility functions that are only called
    dynamically. Reported as informational findings.
    """
    warning_entities: list[FindingEntity] = []
    nx_graph = call_graph.to_networkx()
    total_nodes = nx_graph.number_of_nodes()
    delimiter = call_graph.delimiter

    skipped = 0
    for node_name in nx_graph.nodes:
        node = call_graph.nodes.get(node_name)
        # Skip classes, data entities, and callbacks/anonymous functions —
        # they are not expected to have independent call relationships
        if node and (node.is_class() or node.is_data() or node.is_callback_or_anonymous()):
            skipped += 1
            continue

        # Skip dunder methods and __init__.py functions — these are common
        # false positives because the LSP call graph doesn't capture them
        if _is_dunder_method(node_name, delimiter):
            skipped += 1
            continue
        file_path = node.file_path if node else None
        if _is_init_module_function(file_path):
            skipped += 1
            continue

        in_deg = nx_graph.in_degree(node_name)
        out_deg = nx_graph.out_degree(node_name)

        if in_deg == 0 and out_deg == 0:
            warning_entities.append(
                FindingEntity(
                    entity_name=node_name,
                    file_path=file_path,
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
