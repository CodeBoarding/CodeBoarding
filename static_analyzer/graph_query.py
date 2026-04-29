"""Graph and symbol lookup helpers for semantic incremental analysis."""

from collections import defaultdict
from dataclasses import dataclass, field

import networkx as nx

from static_analyzer.analysis_result import StaticAnalysisResults
from static_analyzer.graph import CallGraph
from static_analyzer.node import Node


@dataclass
class GraphRegionMetadata:
    """Region metadata derived from SCC condensation of call graphs."""

    method_to_scc: dict[str, int] = field(default_factory=dict)
    scc_to_methods: dict[int, set[str]] = field(default_factory=dict)
    scc_to_region: dict[int, int] = field(default_factory=dict)
    method_to_region: dict[str, int] = field(default_factory=dict)


def resolve_method_node(static_analysis: StaticAnalysisResults, qualified_name: str) -> Node | None:
    """Resolve a qualified name across all languages."""
    for language in static_analysis.get_languages():
        try:
            return static_analysis.get_reference(language, qualified_name)
        except (ValueError, FileExistsError):
            _, node = static_analysis.get_loose_reference(language, qualified_name)
            if node is not None:
                return node
    return None


NeighborIndex = tuple[dict[str, list[str]], dict[str, list[str]]]


def build_neighbor_indexes(*cfg_dicts: dict[str, CallGraph]) -> NeighborIndex:
    """Build upstream and downstream adjacency indexes from one or more CFG maps."""
    upstream: dict[str, set[str]] = defaultdict(set)
    downstream: dict[str, set[str]] = defaultdict(set)

    for cfgs in cfg_dicts:
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
    """Build SCC and weak-component metadata for region grouping."""
    graph = nx.DiGraph()
    all_nodes: set[str] = set(upstream_index) | set(downstream_index)

    for neighbors in upstream_index.values():
        all_nodes.update(neighbors)
    for source, neighbors in downstream_index.items():
        all_nodes.add(source)
        all_nodes.update(neighbors)
        for destination in neighbors:
            graph.add_edge(source, destination)

    if all_nodes:
        graph.add_nodes_from(all_nodes)
    if graph.number_of_nodes() == 0:
        return GraphRegionMetadata()

    method_to_scc: dict[str, int] = {}
    scc_to_methods: dict[int, set[str]] = {}
    for scc_id, members in enumerate(nx.strongly_connected_components(graph)):
        member_set = set(members)
        scc_to_methods[scc_id] = member_set
        for method in member_set:
            method_to_scc[method] = scc_id

    condensed = nx.DiGraph()
    condensed.add_nodes_from(scc_to_methods)
    for source, destination in graph.edges():
        source_scc = method_to_scc[source]
        destination_scc = method_to_scc[destination]
        if source_scc != destination_scc:
            condensed.add_edge(source_scc, destination_scc)

    scc_to_region: dict[int, int] = {}
    method_to_region: dict[str, int] = {}
    for region_id, component in enumerate(nx.weakly_connected_components(condensed)):
        for scc_id in component:
            scc_to_region[scc_id] = region_id
            for method in scc_to_methods[scc_id]:
                method_to_region[method] = region_id

    return GraphRegionMetadata(
        method_to_scc=method_to_scc,
        scc_to_methods=scc_to_methods,
        scc_to_region=scc_to_region,
        method_to_region=method_to_region,
    )


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
