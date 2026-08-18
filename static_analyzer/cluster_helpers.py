"""Compatibility imports for clustering helpers moved into the clustering package."""

from static_analyzer.clustering.grouping import (
    combine_cluster_results,
    group_symbols,
    reindex_across_languages,
    reindex_cluster_result,
)
from static_analyzer.clustering.orchestration import build_all_cluster_results

__all__ = [
    "build_all_cluster_results",
    "combine_cluster_results",
    "group_symbols",
    "reindex_across_languages",
    "reindex_cluster_result",
]
