"""Entry point for clustering a call graph."""

from __future__ import annotations

from static_analyzer.graph import CallGraph
from static_analyzer.clustering.engine import cluster_graph
from static_analyzer.clustering.models import ClusterResult
from static_analyzer.constants import ClusteringConfig


class ClusteringService:
    """Clusters a ``CallGraph``.

    Pure: it neither mutates the graph nor caches anything. Callers that want to
    keep a partition store it in the ``ClusterCache`` on their ``LanguageResults``.
    """

    def __init__(
        self,
        target_clusters: int = ClusteringConfig.DEFAULT_TARGET_CLUSTERS,
        min_cluster_size: int = ClusteringConfig.DEFAULT_MIN_CLUSTER_SIZE,
    ) -> None:
        self.target_clusters = target_clusters
        self.min_cluster_size = min_cluster_size

    def cluster(self, graph: CallGraph) -> ClusterResult:
        nx_graph = graph.clustering_networkx()
        return cluster_graph(
            nx_graph,
            delimiter=graph.delimiter,
            target_clusters=self.target_clusters,
            min_cluster_size=self.min_cluster_size,
        )
