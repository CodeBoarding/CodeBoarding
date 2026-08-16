"""Entry point for clustering a call graph."""

from __future__ import annotations

from static_analyzer.cfg import CallGraph, DEFAULT_REFERENCE_KINDS
from static_analyzer.clustering.engine import cluster_graph
from static_analyzer.clustering.models import ClusterResult


class ClusteringService:
    """Clusters a ``CallGraph``.

    Pure: it neither mutates the graph nor caches anything. Callers that want to
    keep a partition store it in the ``ClusterCache`` on their ``LanguageResults``.
    """

    def cluster(self, graph: CallGraph) -> ClusterResult:
        return cluster_graph(graph.to_networkx(DEFAULT_REFERENCE_KINDS), delimiter=graph.delimiter)
