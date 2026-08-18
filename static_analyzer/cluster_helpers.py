"""Compatibility imports for clustering helpers moved into the clustering package."""

from static_analyzer.clustering.grouping import (
    REGROUP_DRIFT_BUDGET,
    SUBCOMPONENTS_MAX,
    SUBCOMPONENTS_MIN,
    TOP_LEVEL_COMPONENTS_MAX,
    TOP_LEVEL_COMPONENTS_MIN,
    AnchoredGrouping,
    _absorb_leftovers,
    _build_meta_graph,
    _method_counts,
    _modularity,
    _seeds_from_partition,
    anchored_grouping,
    combine_cluster_results,
    group_symbols,
    reindex_across_languages,
    reindex_cluster_result,
    supercluster_by_modularity_peak,
    supercluster_leaf_ids,
)
from static_analyzer.clustering.orchestration import build_all_cluster_results

__all__ = [
    "REGROUP_DRIFT_BUDGET",
    "SUBCOMPONENTS_MAX",
    "SUBCOMPONENTS_MIN",
    "TOP_LEVEL_COMPONENTS_MAX",
    "TOP_LEVEL_COMPONENTS_MIN",
    "AnchoredGrouping",
    "anchored_grouping",
    "build_all_cluster_results",
    "combine_cluster_results",
    "group_symbols",
    "reindex_across_languages",
    "reindex_cluster_result",
    "supercluster_by_modularity_peak",
    "supercluster_leaf_ids",
]
