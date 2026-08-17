"""Seeded-Leiden clustering with a level-up search over a graph the caller exports."""

from __future__ import annotations

import logging
from collections import defaultdict
from enum import StrEnum
from typing import NamedTuple

import networkx as nx

from static_analyzer.clustering.models import ClusterResult
from static_analyzer.constants import ClusteringConfig
from static_analyzer.leiden_utils import find_partition

logger = logging.getLogger(__name__)


class Level(StrEnum):
    """Abstraction levels the search tries, in order. ``RAW`` is the un-abstracted graph."""

    RAW = ""
    CLASS = "class"
    FILE = "file"


class Candidate(NamedTuple):
    """One scored Leiden partition, tagged with the level it was found at."""

    communities: list[set[str]]
    level: Level
    score: float

    @property
    def strategy(self) -> str:
        """Name recorded on the ``ClusterResult`` and persisted in analysis.json."""
        return "leiden" if self.level is Level.RAW else f"leiden_level_{self.level}"


def cluster_graph(nx_graph: nx.DiGraph, *, delimiter: str) -> ClusterResult:
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

    for level in Level:
        work_graph = nx_graph
        if level:
            work_graph = _abstract_at_level(nx_graph, level, delimiter)
            if work_graph.number_of_nodes() == 0:
                continue

        candidate = _leiden_candidate(work_graph, level, total_nodes)
        if level:
            candidate = _map_candidate_to_original(candidate, nx_graph, delimiter, total_nodes)

        all_candidates.append(candidate)

        coverage = _coverage(candidate.communities, total_nodes)
        logger.info(
            f"Level {level or 'raw'}: best={candidate.strategy} score={candidate.score:.3f} coverage={coverage:.3f}"
        )
        if coverage >= ClusteringConfig.MIN_COVERAGE_RATIO:
            break

    # Level.RAW never abstracts, so the loop always scores at least one candidate.
    best = max(all_candidates, key=lambda c: c.score)
    if best.score > 0.0:
        return _build_result(best.communities, best.strategy, nx_graph)

    logger.warning("All clustering strategies scored 0, falling back to connected components")
    # Every component is kept: dropping any would remove its members from the partition
    # entirely, and downstream snapshots could then not render those methods.
    components = list(nx.connected_components(nx_graph.to_undirected()))
    return _build_result([set(c) for c in components], "connected_components", nx_graph)


def _abstract_node_name(node_name: str, level: Level, delimiter: str) -> str:
    parts = node_name.split(delimiter)

    if level is Level.CLASS and len(parts) > 1:
        return delimiter.join(parts[:-1])
    if level is Level.FILE and len(parts) > 2:
        return delimiter.join(parts[:-2])
    return node_name


def _score_clustering(communities: list[set[str]], total_nodes: int) -> float:
    """Score clustering from 0.0 to 1.0. Coverage is primary, cluster count is a penalty."""
    if not communities or total_nodes == 0:
        return 0.0

    valid_clusters = [c for c in communities if len(c) >= ClusteringConfig.DEFAULT_MIN_CLUSTER_SIZE]
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

    # Never read: find_partition is called without ``weight=``. Passing it moves every partition.
    edge_weights: dict[tuple[str, str], int] = defaultdict(int)
    for src, dst in graph.edges():
        a_src, a_dst = node_map[src], node_map[dst]
        if a_src != a_dst:
            edge_weights[(a_src, a_dst)] += 1

    for (src, dst), weight in edge_weights.items():
        abstracted.add_edge(src, dst, weight=weight)

    return abstracted


def _leiden_candidate(graph: nx.DiGraph, level: Level, total_nodes: int) -> Candidate:
    """Score one seeded-Leiden partition of ``graph``; a failure scores 0 and loses.

    Why seeded: Leiden is non-deterministic otherwise, and cluster IDs persisted in
    analysis.json must reproduce on the next run.
    """
    communities: list[set[str]] = []
    try:
        communities = find_partition(graph, seed=ClusteringConfig.CLUSTERING_SEED)
    except Exception:
        logger.warning(f"Leiden failed at level {level or 'raw'}; scoring 0", exc_info=True)
    score = _score_clustering(communities, total_nodes)
    logger.debug(f"leiden: score={score:.3f}, clusters={len(communities)}")
    return Candidate(communities, level, score)


def _map_candidate_to_original(
    candidate: Candidate,
    original_graph: nx.DiGraph,
    delimiter: str,
    total_nodes: int,
) -> Candidate:
    """Map an abstract-level community result back to original node names and re-score."""
    abstract_to_original: dict[str, list[str]] = defaultdict(list)
    for node in original_graph.nodes():
        abstract_to_original[_abstract_node_name(node, candidate.level, delimiter)].append(node)

    original_communities: list[set[str]] = []
    for community in candidate.communities:
        orig: set[str] = set()
        for abstract_node in community:
            orig.update(abstract_to_original[abstract_node])
        if orig:
            original_communities.append(orig)
    score = _score_clustering(original_communities, total_nodes)
    return Candidate(original_communities, candidate.level, score)


def _coverage(communities: list[set[str]], total_nodes: int) -> float:
    """Fraction of nodes landing in clusters that meet the minimum size."""
    if total_nodes == 0:
        return 0.0
    valid = [c for c in communities if len(c) >= ClusteringConfig.DEFAULT_MIN_CLUSTER_SIZE]
    return sum(len(c) for c in valid) / total_nodes


def _build_result(
    communities: list[set[str]],
    strategy: str,
    nx_graph: nx.DiGraph,
) -> ClusterResult:
    valid_communities = [c for c in communities if len(c) >= ClusteringConfig.DEFAULT_MIN_CLUSTER_SIZE]
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
