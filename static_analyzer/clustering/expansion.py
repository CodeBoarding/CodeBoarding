"""Deterministic rules for deciding whether a clustered scope should split."""

from collections.abc import Mapping

from static_analyzer.clustering.models import METHOD_LEVEL_STRATEGY, ClusterResult

MIN_METHODS_TO_EXPAND = 30
MAX_LEAF_FILES = 12
MAX_LEAF_METHODS = 120
EXPAND_MODULARITY_THRESHOLD = 0.15


def scope_load(method_count: int, file_count: int) -> float:
    """How full a scope is against the leaf ceiling."""
    return max(method_count / MAX_LEAF_METHODS, file_count / MAX_LEAF_FILES)


def scope_is_separable(
    partitions: Mapping[str, ClusterResult],
    modularity: float,
    load: float,
    min_methods: int = MIN_METHODS_TO_EXPAND,
) -> bool:
    """Whether a scope's natural clustering and modularity justify a split."""
    total_methods = sum(len(members) for partition in partitions.values() for members in partition.clusters.values())
    if total_methods < min_methods:
        return False
    if all(partition.strategy == METHOD_LEVEL_STRATEGY for partition in partitions.values()):
        return False
    required = EXPAND_MODULARITY_THRESHOLD * max(0.0, 1.0 - load)
    return modularity >= required
