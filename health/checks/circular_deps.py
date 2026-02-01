import logging

import networkx as nx

from health.models import CircularDependencyCheck, HealthCheckConfig

logger = logging.getLogger(__name__)


def check_circular_dependencies(package_dependencies: dict, config: HealthCheckConfig) -> CircularDependencyCheck:
    """E6: Detect circular dependencies at the package level.

    Circular dependencies make the system rigid, hard to modify, and
    difficult to test in isolation.
    """
    cycles: list[str] = []

    graph = nx.DiGraph()
    for package, info in package_dependencies.items():
        graph.add_node(package)
        imports = info.get("imports", [])
        if isinstance(imports, dict):
            imports = list(imports.keys())
        for imported in imports:
            if imported in package_dependencies:
                graph.add_edge(package, imported)

    total_packages = graph.number_of_nodes()
    packages_in_cycles: set[str] = set()

    try:
        for cycle in nx.simple_cycles(graph):
            if len(cycles) >= config.max_cycles_reported:
                break
            packages_in_cycles.update(cycle)
            cycles.append(" -> ".join(cycle + [cycle[0]]))
    except nx.NetworkXError:
        logger.warning("Error while detecting cycles in package dependency graph")

    return CircularDependencyCheck(
        check_name="circular_dependencies",
        description="Detects circular dependencies between packages",
        cycles=cycles,
        packages_checked=total_packages,
        packages_in_cycles=len(packages_in_cycles),
    )
