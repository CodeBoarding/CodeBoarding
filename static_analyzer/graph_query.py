"""Graph and symbol lookup helpers for semantic incremental analysis."""

from collections import defaultdict
from dataclasses import dataclass, field

import networkx as nx

from static_analyzer.analysis_result import StaticAnalysisResults
from static_analyzer.graph import CallGraph
from static_analyzer.node import Node


@dataclass
class GraphRegionMetadata:
    method_to_region: dict[str, int] = field(default_factory=dict)


def resolve_method_node(static_analysis: StaticAnalysisResults, qualified_name: str) -> Node | None:
    """Resolve a qualified name across all languages, exact match preferred over loose."""
    languages = list(static_analysis.get_languages())
    for language in languages:
        try:
            return static_analysis.get_reference(language, qualified_name)
        except (ValueError, FileExistsError):
            continue
    for language in languages:
        _, node = static_analysis.get_loose_reference(language, qualified_name)
        if node is not None:
            return node
    return None


NeighborIndex = tuple[dict[str, list[str]], dict[str, list[str]]]


def build_neighbor_indexes(cfgs: dict[str, CallGraph]) -> NeighborIndex:
    """Build upstream and downstream adjacency indexes from a CFG map."""
    upstream: dict[str, set[str]] = defaultdict(set)
    downstream: dict[str, set[str]] = defaultdict(set)

    for cfg in cfgs.values():
        for qualified_name, node in cfg.nodes.items():
            if node.methods_called_by_me:
                downstream[qualified_name].update(node.methods_called_by_me)
        for edge in cfg.edges:
            upstream[edge.get_destination()].add(edge.get_source())

    return (
        {qualified_name: sorted(neighbors) for qualified_name, neighbors in upstream.items()},
        {qualified_name: sorted(neighbors) for qualified_name, neighbors in downstream.items()},
    )


def get_neighbors(
    upstream_index: dict[str, list[str]],
    downstream_index: dict[str, list[str]],
    qualified_name: str,
) -> tuple[list[str], list[str]]:
    """Return callers and callees for *qualified_name*."""
    return upstream_index.get(qualified_name, []), downstream_index.get(qualified_name, [])


def build_graph_region_metadata(
    upstream_index: dict[str, list[str]],
    downstream_index: dict[str, list[str]],
) -> GraphRegionMetadata:
    """Build weak-component metadata for region grouping."""
    graph = nx.DiGraph()
    all_nodes: set[str] = set(upstream_index) | set(downstream_index)
    for neighbors in upstream_index.values():
        all_nodes.update(neighbors)
    for source, neighbors in downstream_index.items():
        all_nodes.update(neighbors)
        for destination in neighbors:
            graph.add_edge(source, destination)
    graph.add_nodes_from(all_nodes)

    method_to_region: dict[str, int] = {}
    for region_id, component in enumerate(nx.weakly_connected_components(graph)):
        for method in component:
            method_to_region[method] = region_id

    return GraphRegionMetadata(method_to_region=method_to_region)


def determine_region_key(
    qualified_name: str,
    file_path: str,
    graph_metadata: GraphRegionMetadata,
) -> tuple[str, bool]:
    """Return a region key for a method and whether it is graph-backed."""
    region_id = graph_metadata.method_to_region.get(qualified_name)
    if region_id is None:
        return f"file:{file_path}", False
    return f"region:{region_id}", True
