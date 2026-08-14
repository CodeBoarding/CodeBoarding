"""Clustering of call graphs: the search, the result types, and method lineage.

``engine`` is the search over an exported ``nx.DiGraph``; ``models`` holds the
result types; ``method_cluster_paths`` tracks each method's scoped cluster ancestry.
"""

from static_analyzer.clustering.engine import cluster_graph
from static_analyzer.clustering.method_cluster_paths import MethodClusterPaths
from static_analyzer.clustering.models import METHOD_LEVEL_STRATEGY, ClusterResult

__all__ = [
    "METHOD_LEVEL_STRATEGY",
    "ClusterResult",
    "MethodClusterPaths",
    "cluster_graph",
]
