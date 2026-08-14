"""Seeded-Leiden clustering with a level-up search over an exported graph.

Operates on ``nx.DiGraph`` only — it never sees a ``CallGraph``, so the search
can be exercised on any graph. ``ClusteringService`` does the export.
"""

from __future__ import annotations

import logging
from collections import defaultdict

import networkx as nx

from static_analyzer.clustering.models import ClusterResult
from static_analyzer.constants import ClusteringConfig
from static_analyzer.leiden_utils import find_partition

logger = logging.getLogger(__name__)

# Abstraction levels tried in order; "" is the raw method-level graph.
_LEVELS: tuple[str, ...] = ("", "class", "file")

# (communities, strategy name, score)
Candidate = tuple[list[set[str]], str, float]


def cluster_graph(
    nx_graph: nx.DiGraph,
    *,
    delimiter: str = ClusteringConfig.QUALIFIED_NAME_DELIMITER,
    target_clusters: int = ClusteringConfig.DEFAULT_TARGET_CLUSTERS,
    min_cluster_size: int = ClusteringConfig.DEFAULT_MIN_CLUSTER_SIZE,
) -> ClusterResult:
    """Cluster ``nx_graph``, levelling up until the partition covers enough of it.

    Scores a Leiden partition at each abstraction level (raw, class, file) and stops
    at the first to reach ``MIN_COVERAGE_RATIO``, else keeps the best-scoring level.
    Falls back to connected components if every level scores zero.
    """
    if nx_graph.number_of_nodes() == 0:
        logger.warning("No nodes available for clustering.")
        return ClusterResult(strategy="empty")

    total_nodes = nx_graph.number_of_nodes()
    all_candidates: list[Candidate] = []

    for level in _LEVELS:
        work_graph = nx_graph
        if level:
            work_graph = _abstract_at_level(nx_graph, level, delimiter)
            if work_graph.number_of_nodes() == 0:
                continue

        candidate = _leiden_candidate(work_graph, min_cluster_size, total_nodes)
        if level:
            candidate = _map_candidate_to_original(candidate, nx_graph, level, delimiter, min_cluster_size, total_nodes)

        all_candidates.append(candidate)

        communities, strategy, score = candidate
        coverage = _coverage(communities, min_cluster_size, total_nodes)
        logger.info(f"Level {level or 'raw'}: best={strategy} score={score:.3f} coverage={coverage:.3f}")
        if coverage >= ClusteringConfig.MIN_COVERAGE_RATIO:
            break

    if all_candidates:
        best_communities, best_strategy, best_score = max(all_candidates, key=lambda c: c[2])
        if best_score > 0.0:
            return _build_result(best_communities, best_strategy, min_cluster_size, nx_graph)

    logger.warning("All clustering strategies scored 0, falling back to connected components")
    components = list(nx.connected_components(nx_graph.to_undirected()))
    return _build_result(
        [set(c) for c in components[:target_clusters]], "connected_components", min_cluster_size, nx_graph
    )


def _abstract_node_name(node_name: str, level: str, delimiter: str) -> str:
    parts = node_name.split(delimiter)

    if level == "class" and len(parts) > 1:
        return delimiter.join(parts[:-1])
    elif level == "file" and len(parts) > 2:
        return delimiter.join(parts[:-2])
    elif level == "package" and len(parts) > 3:
        return parts[0]
    else:
        return node_name


def _score_clustering(communities: list[set[str]], min_cluster_size: int, total_nodes: int) -> float:
    """Score clustering from 0.0 to 1.0. Coverage is primary, cluster count is a penalty."""
    if not communities or total_nodes == 0:
        return 0.0

    valid_clusters = [c for c in communities if len(c) >= min_cluster_size]
    if not valid_clusters:
        return 0.0

    # Coverage: fraction of nodes in valid clusters (primary driver)
    covered_nodes = sum(len(c) for c in valid_clusters)
    coverage_score = covered_nodes / total_nodes

    # Cluster count penalty: ideal range [total_nodes/20, total_nodes/5]
    cluster_count = len(valid_clusters)
    ideal_min = max(2, total_nodes // 20)
    ideal_max = max(ideal_min + 1, total_nodes // 5)

    if ideal_min <= cluster_count <= ideal_max:
        cluster_count_penalty = 1.0
    elif cluster_count < ideal_min:
        cluster_count_penalty = cluster_count / ideal_min
    else:
        overshoot = cluster_count - ideal_max
        cluster_count_penalty = max(0.0, 1.0 - overshoot / ideal_max)

    return coverage_score * cluster_count_penalty


def _abstract_at_level(graph: nx.DiGraph, level: str, delimiter: str) -> nx.DiGraph:
    """Create abstracted graph by grouping nodes at the given level."""
    abstracted = nx.DiGraph()
    node_map: dict[str, str] = {}

    for node in graph.nodes():
        abstract_name = _abstract_node_name(node, level, delimiter)
        node_map[node] = abstract_name
        if abstract_name not in abstracted:
            abstracted.add_node(abstract_name)

    edge_weights: dict[tuple[str, str], int] = defaultdict(int)
    for src, dst in graph.edges():
        a_src, a_dst = node_map[src], node_map[dst]
        if a_src != a_dst:
            edge_weights[(a_src, a_dst)] += 1

    for (src, dst), weight in edge_weights.items():
        abstracted.add_edge(src, dst, weight=weight)

    return abstracted


def _leiden_candidate(graph: nx.DiGraph, min_cluster_size: int, total_nodes: int) -> Candidate:
    """Score one seeded-Leiden partition of ``graph``; a failure scores 0 and loses.

    Why seeded: Leiden is non-deterministic otherwise, and cluster IDs persisted in
    analysis.json must reproduce on the next run.
    """
    communities: list[set[str]] = []
    try:
        communities = find_partition(graph, seed=ClusteringConfig.CLUSTERING_SEED)
    except Exception as e:
        logger.debug(f"Leiden failed: {e}")
    score = _score_clustering(communities, min_cluster_size, total_nodes)
    logger.debug(f"leiden: score={score:.3f}, clusters={len(communities)}")
    return communities, "leiden", score


def _map_candidate_to_original(
    candidate: Candidate,
    original_graph: nx.DiGraph,
    level: str,
    delimiter: str,
    min_cluster_size: int,
    total_nodes: int,
) -> Candidate:
    """Map an abstract-level community result back to original node names and re-score."""
    abstract_to_original: dict[str, list[str]] = defaultdict(list)
    for node in original_graph.nodes():
        abstract_to_original[_abstract_node_name(node, level, delimiter)].append(node)

    communities, algo, _ = candidate
    original_communities: list[set[str]] = []
    for community in communities:
        orig: set[str] = set()
        for abstract_node in community:
            orig.update(abstract_to_original[abstract_node])
        if orig:
            original_communities.append(orig)
    score = _score_clustering(original_communities, min_cluster_size, total_nodes)
    return original_communities, f"{algo}_level_{level}", score


def _coverage(communities: list[set[str]], min_cluster_size: int, total_nodes: int) -> float:
    """Fraction of nodes landing in clusters that meet ``min_cluster_size``."""
    if total_nodes == 0:
        return 0.0
    valid = [c for c in communities if len(c) >= min_cluster_size]
    return sum(len(c) for c in valid) / total_nodes


def _build_result(
    communities: list[set[str]],
    strategy: str,
    min_cluster_size: int,
    nx_graph: nx.DiGraph,
) -> ClusterResult:
    valid_communities = [c for c in communities if len(c) >= min_cluster_size]
    sorted_communities = sorted(valid_communities, key=len, reverse=True)

    clusters: dict[int, set[str]] = {}
    file_to_clusters: dict[str, set[int]] = defaultdict(set)
    cluster_to_files: dict[int, set[str]] = defaultdict(set)

    for cluster_id, nodes in enumerate(sorted_communities, start=1):
        clusters[cluster_id] = set(nodes)
        for node_name in nodes:
            if node_name in nx_graph.nodes:
                file_path = nx_graph.nodes[node_name].get("file_path")
                if file_path:
                    file_to_clusters[file_path].add(cluster_id)
                    cluster_to_files[cluster_id].add(file_path)

    logger.info(f"Clustered {nx_graph.number_of_nodes()} nodes into {len(clusters)} clusters using {strategy}")

    return ClusterResult(
        clusters=clusters,
        file_to_clusters=dict(file_to_clusters),
        cluster_to_files=dict(cluster_to_files),
        strategy=strategy,
    )
