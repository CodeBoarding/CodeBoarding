"""Clustering of call graphs: recursive scopes, Leiden partitions, and cached results.

``service`` is the entry point; ``engine`` runs Leiden over an exported
``nx.DiGraph``; ``models`` holds the result types; ``cache`` holds the per-language
state that ``LanguageResults`` owns.
"""

from static_analyzer.clustering.cache import ClusterCache, ClusterScopeLineage, record_cluster_hierarchy
from static_analyzer.clustering.models import (
    ClusterConnectionEdge,
    ClusterGroup,
    ClusterResult,
    ClusterScopeResult,
    GroupConnection,
)

__all__ = [
    "ClusterCache",
    "ClusterScopeLineage",
    "record_cluster_hierarchy",
    "ClusterConnectionEdge",
    "ClusterGroup",
    "ClusterResult",
    "ClusterScopeResult",
    "GroupConnection",
]
