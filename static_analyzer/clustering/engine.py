"""Deterministic Leiden clustering over a graph supplied by the caller."""

from __future__ import annotations

import logging
from collections import defaultdict

import networkx as nx

from static_analyzer.clustering.models import ClusterResult
from static_analyzer.config import ClusteringConfig
from static_analyzer.leiden_utils import find_partition

logger = logging.getLogger(__name__)


def cluster_graph(nx_graph: nx.DiGraph) -> ClusterResult:
    """Run seeded Leiden once and return all communities."""
    communities: list[set[str]] = find_partition(nx_graph, seed=ClusteringConfig.CLUSTERING_SEED)
    return _build_result(communities, nx_graph)


def _build_result(
    communities: list[set[str]],
    nx_graph: nx.DiGraph,
) -> ClusterResult:
    sorted_communities = sorted(communities, key=lambda community: (-len(community), sorted(community)))

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

    logger.info("Clustered %d nodes into %d clusters using Leiden", nx_graph.number_of_nodes(), len(clusters))

    return ClusterResult(
        clusters=clusters,
        file_to_clusters=dict(file_to_clusters),
        cluster_to_files=dict(cluster_to_files),
        strategy="leiden",
    )
