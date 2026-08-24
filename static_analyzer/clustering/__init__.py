"""Clustering of call graphs: the partition types, the search, and where results are kept.

``service`` is the entry point; ``engine`` is the Leiden search over an exported
``nx.DiGraph``; ``models`` holds the result types; ``cache`` holds the per-language
state that ``LanguageResults`` owns.
"""

from static_analyzer.clustering.cache import ClusterCache, ClusterScopeLineage
from static_analyzer.clustering.models import (
    METHOD_LEVEL_STRATEGY,
    ClusterConnectionEdge,
    ClusterGroup,
    ClusterResult,
    ClusterScopeResult,
    GroupConnection,
)

__all__ = [
    "METHOD_LEVEL_STRATEGY",
    "ClusterCache",
    "ClusterScopeLineage",
    "ClusterConnectionEdge",
    "ClusterGroup",
    "ClusterResult",
    "ClusterScopeResult",
    "GroupConnection",
]
