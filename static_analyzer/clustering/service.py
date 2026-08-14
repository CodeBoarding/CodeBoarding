"""Entry point for clustering a call graph."""

from __future__ import annotations

from static_analyzer.cfg import CallGraph
from static_analyzer.clustering.engine import cluster_graph
from static_analyzer.clustering.models import ClusterResult
from static_analyzer.constants import ClusteringConfig


class ClusteringService:
    """Clusters a ``CallGraph``.

    Pure: it neither mutates the graph nor caches anything. Callers that want to
    keep a partition store it in the ``ClusterCache`` on their ``LanguageResults``.
    """

    def __init__(self) -> None:
        self.target_clusters = ClusteringConfig.DEFAULT_TARGET_CLUSTERS
        self.min_cluster_size = ClusteringConfig.DEFAULT_MIN_CLUSTER_SIZE

    def cluster(self, graph: CallGraph) -> ClusterResult:
        nx_graph = graph.to_networkx()
        return cluster_graph(
            nx_graph,
            delimiter=graph.delimiter,
            target_clusters=self.target_clusters,
            min_cluster_size=self.min_cluster_size,
        )
