"""Deterministic rules for deciding whether a clustered scope should split."""

from collections.abc import Mapping

from static_analyzer.config import ClusteringConfig
from static_analyzer.clustering.models import METHOD_LEVEL_STRATEGY, ClusterResult


def scope_load(method_count: int, file_count: int) -> float:
    """How full a scope is against the leaf ceiling."""
    return max(
        method_count / ClusteringConfig.MAX_LEAF_METHODS,
        file_count / ClusteringConfig.MAX_LEAF_FILES,
    )


def scope_is_separable(
    leaf_clusters_by_language: Mapping[str, ClusterResult],
    modularity: float,
    load: float,
    method_count: int,
    min_methods: int = ClusteringConfig.MIN_METHODS_TO_EXPAND,
) -> bool:
    """Whether a scope's natural clustering and modularity justify a split."""
    if method_count < min_methods:
        return False
    if all(cluster_result.strategy == METHOD_LEVEL_STRATEGY for cluster_result in leaf_clusters_by_language.values()):
        return False
    required = ClusteringConfig.EXPAND_MODULARITY_THRESHOLD * max(0.0, 1.0 - load)
    return modularity >= required
