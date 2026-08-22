"""Clustering of call graphs: the partition types, the search, and where results are kept.

``service`` is the entry point; ``engine`` is the Leiden search over an exported
``nx.DiGraph``; ``models`` holds the result types; ``cache`` holds the per-language
state that ``LanguageResults`` owns.
"""

from static_analyzer.clustering.cache import ClusterCache
from static_analyzer.config import DEFAULT_GROUPING_CONFIG, SUBCOMPONENT_GROUPING_CONFIG, GroupingConfig
from clustering_ids import ClusterId, ComponentId, GroupId, ScopeId
from static_analyzer.clustering.method_cluster_paths import MethodClusterPaths
from static_analyzer.clustering.models import (
    METHOD_LEVEL_STRATEGY,
    AnchoredGrouping,
    ClusterConnectionEdge,
    ClusterGroup,
    ClusterResult,
    ClusterScopeResult,
    GroupConnection,
)
from static_analyzer.clustering.service import ClusteringService, LeafClustersUnavailableError

__all__ = [
    "DEFAULT_GROUPING_CONFIG",
    "METHOD_LEVEL_STRATEGY",
    "SUBCOMPONENT_GROUPING_CONFIG",
    "AnchoredGrouping",
    "ClusterCache",
    "ClusterConnectionEdge",
    "ClusterGroup",
    "ClusterResult",
    "ClusterScopeResult",
    "ClusteringService",
    "ClusterId",
    "ComponentId",
    "GroupId",
    "GroupConnection",
    "GroupingConfig",
    "LeafClustersUnavailableError",
    "MethodClusterPaths",
    "ScopeId",
]
