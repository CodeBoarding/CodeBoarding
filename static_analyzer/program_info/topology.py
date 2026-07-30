"""Deterministic structural topology analysis."""

from dataclasses import dataclass

import networkx as nx


@dataclass(frozen=True)
class StrongRegion:
    members: tuple[str, ...]
    cyclic: bool
    layer: int


@dataclass(frozen=True)
class TopologyFacts:
    regions: tuple[StrongRegion, ...]
    sources: tuple[str, ...]
    sinks: tuple[str, ...]
    bridges: tuple[str, ...]
    maximum_depth: int


def analyze_topology(graph: nx.DiGraph) -> TopologyFacts:
    """Analyze SCCs and the condensation DAG without depending on insertion order."""
    simple = nx.DiGraph()
    simple.add_nodes_from(sorted(graph.nodes))
    simple.add_edges_from(sorted(graph.edges))
    components = sorted((tuple(sorted(part)) for part in nx.strongly_connected_components(simple)), key=lambda x: x)
    owner = {name: index for index, part in enumerate(components) for name in part}
    dag = nx.DiGraph()
    dag.add_nodes_from(range(len(components)))
    dag.add_edges_from(
        sorted({(owner[source], owner[target]) for source, target in simple.edges if owner[source] != owner[target]})
    )
    layers: dict[int, int] = {}
    for node in nx.lexicographical_topological_sort(dag, key=lambda value: components[value]):
        layers[node] = max((layers[parent] + 1 for parent in dag.predecessors(node)), default=0)
    regions = tuple(
        StrongRegion(part, len(part) > 1 or any(simple.has_edge(name, name) for name in part), layers[index])
        for index, part in enumerate(components)
    )
    sources = tuple(name for name in sorted(simple) if simple.in_degree(name) == 0)
    sinks = tuple(name for name in sorted(simple) if simple.out_degree(name) == 0)
    undirected = simple.to_undirected()
    bridges = tuple(sorted(nx.articulation_points(undirected))) if len(simple) else ()
    return TopologyFacts(regions, sources, sinks, bridges, max(layers.values(), default=0))
