"""Clustering of call graphs: the result types and method lineage.

``models`` holds the result types; ``method_cluster_paths`` tracks each method's
scoped cluster ancestry.
"""

from static_analyzer.clustering.method_cluster_paths import MethodClusterPaths
from static_analyzer.clustering.models import METHOD_LEVEL_STRATEGY, ClusterResult

__all__ = [
    "METHOD_LEVEL_STRATEGY",
    "ClusterResult",
    "MethodClusterPaths",
]
