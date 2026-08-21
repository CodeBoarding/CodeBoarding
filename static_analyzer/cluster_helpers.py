"""Compatibility imports for clustering helpers moved into the clustering package."""

from static_analyzer.clustering.grouping import (
    combine_cluster_results,
    group_symbols,
    reindex_across_languages,
    reindex_cluster_result,
)

__all__ = [
    "combine_cluster_results",
    "group_symbols",
    "reindex_across_languages",
    "reindex_cluster_result",
]
