"""Clustering search over an exported graph.

Operates on ``nx.DiGraph`` only — it never sees a ``CallGraph``, so the search can
be exercised on any graph. The caller does the export.
"""

import logging
from collections import defaultdict
from enum import StrEnum

import networkx as nx
import networkx.algorithms.community as nx_comm

from static_analyzer.clustering.models import ClusterResult
from static_analyzer.constants import ClusteringConfig
from static_analyzer.leiden_utils import find_partition

logger = logging.getLogger(__name__)


class Level(StrEnum):
    """Abstraction levels the search tries, in order. ``RAW`` is the un-abstracted graph."""

    RAW = ""
    CLASS = "class"
    FILE = "file"


def cluster_graph(
    nx_graph: nx.DiGraph,
    delimiter: str,
    target_clusters: int = ClusteringConfig.DEFAULT_TARGET_CLUSTERS,
    min_cluster_size: int = ClusteringConfig.DEFAULT_MIN_CLUSTER_SIZE,
) -> ClusterResult:
    """Cluster the graph using a try-all-then-level-up approach.

    Flow: try all algorithms at each abstraction level (raw, class, file).
    If coverage >= 50% at any level, stop and return the best result.
    Falls back to connected components if everything fails.
    """
    if nx_graph.number_of_nodes() == 0:
        logger.warning("No nodes available for clustering.")
        return ClusterResult(strategy="empty")

    total_nodes = nx_graph.number_of_nodes()
    all_candidates: list[tuple[list[set[str]], str, float]] = []

    for level in Level:
        work_graph = nx_graph
        if level:
            work_graph = _abstract_at_level(nx_graph, level, delimiter)
            if work_graph.number_of_nodes() == 0:
                continue

        candidates = _try_all_algorithms(work_graph, min_cluster_size, total_nodes)

        if level:
            candidates = _map_candidates_to_original(
                candidates, nx_graph, level, delimiter, min_cluster_size, total_nodes
            )

        all_candidates.extend(candidates)

        # Check if best coverage at this level is good enough
        if candidates:
            best = max(candidates, key=lambda c: c[2])
            best_coverage = _coverage(best[0], min_cluster_size, total_nodes)
            logger.info(f"Level {level or 'raw'}: best={best[1]} score={best[2]:.3f} coverage={best_coverage:.3f}")
            if best_coverage >= ClusteringConfig.MIN_COVERAGE_RATIO:
                break

    # Pick overall best
    if all_candidates:
        best_communities, best_strategy, best_score = max(all_candidates, key=lambda c: c[2])
        if best_score > 0.0:
            return _build_result(best_communities, best_strategy, min_cluster_size, nx_graph)

    # Absolute fallback: connected components
    logger.warning("All clustering strategies scored 0, falling back to connected components")
    components = list(nx.connected_components(nx_graph.to_undirected()))
    return _build_result(
        [set(c) for c in components[:target_clusters]], "connected_components", min_cluster_size, nx_graph
    )


def _abstract_node_name(node_name: str, level: Level, delimiter: str) -> str:
    parts = node_name.split(delimiter)

    if level is Level.CLASS and len(parts) > 1:
        return delimiter.join(parts[:-1])
    if level is Level.FILE and len(parts) > 2:
        return delimiter.join(parts[:-2])
    return node_name


def _cluster_with_algorithm(graph: nx.DiGraph, algorithm: str) -> list[set[str]]:
    # Use a fixed seed for reproducibility - Leiden/Louvain are non-deterministic without it
    if algorithm == "leiden":
        return find_partition(graph, seed=ClusteringConfig.CLUSTERING_SEED)
    elif algorithm == "louvain":
        return list(nx_comm.louvain_communities(graph, seed=ClusteringConfig.CLUSTERING_SEED))
    elif algorithm == "greedy_modularity":
        return list(nx.community.greedy_modularity_communities(graph))
    else:
        logger.warning(f"Algorithm {algorithm} not supported, defaulting to leiden")
        return find_partition(graph, seed=ClusteringConfig.CLUSTERING_SEED)


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


def _abstract_at_level(graph: nx.DiGraph, level: Level, delimiter: str) -> nx.DiGraph:
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


def _try_all_algorithms(
    graph: nx.DiGraph,
    min_cluster_size: int,
    total_nodes: int,
) -> list[tuple[list[set[str]], str, float]]:
    """Run Leiden and return a single scored candidate.

    Returned as a list so ``cluster_graph``'s cross-level pooling stays uniform.
    """
    candidates: list[tuple[list[set[str]], str, float]] = []
    try:
        communities = _cluster_with_algorithm(graph, "leiden")
        score = _score_clustering(communities, min_cluster_size, total_nodes)
        candidates.append((communities, "leiden", score))
        logger.debug(f"leiden: score={score:.3f}, clusters={len(communities)}")
    except Exception as e:
        logger.debug(f"Algorithm leiden failed: {e}")
    return candidates


def _map_candidates_to_original(
    candidates: list[tuple[list[set[str]], str, float]],
    original_graph: nx.DiGraph,
    level: Level,
    delimiter: str,
    min_cluster_size: int,
    total_nodes: int,
) -> list[tuple[list[set[str]], str, float]]:
    """Map abstract community results back to original node names and re-score."""
    abstract_to_original: dict[str, list[str]] = defaultdict(list)
    for node in original_graph.nodes():
        abstract_to_original[_abstract_node_name(node, level, delimiter)].append(node)

    mapped: list[tuple[list[set[str]], str, float]] = []
    for communities, algo, _ in candidates:
        original_communities: list[set[str]] = []
        for community in communities:
            orig: set[str] = set()
            for abstract_node in community:
                orig.update(abstract_to_original[abstract_node])
            if orig:
                original_communities.append(orig)
        new_score = _score_clustering(original_communities, min_cluster_size, total_nodes)
        mapped.append((original_communities, f"{algo}_level_{level}", new_score))
    return mapped


def _coverage(communities: list[set[str]], min_cluster_size: int, total_nodes: int) -> float:
    """Calculate coverage: fraction of nodes in valid clusters."""
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
    """Build ClusterResult from communities."""
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
