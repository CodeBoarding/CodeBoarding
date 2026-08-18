"""Root-scope clustering orchestration and cache synchronization."""

import logging
from collections.abc import Callable, Mapping

from static_analyzer.analysis_result import StaticAnalysisResults
from static_analyzer.cfg import CallGraph
from static_analyzer.clustering.grouping import reindex_cluster_result
from static_analyzer.clustering.models import ClusterResult, ClusterScopeInput, ClusterScopeResult
from static_analyzer.clustering.service import ClusteringService
from static_analyzer.config import Language

logger = logging.getLogger(__name__)


def _unseeded_scope(_scope_id: str, _graphs: Mapping[str, CallGraph]) -> ClusterScopeInput:
    return ClusterScopeInput()


def build_clustering_hierarchy(
    static_analysis: StaticAnalysisResults,
    max_depth: int,
    *,
    root_leaf_clusters: Mapping[str, ClusterResult] | None = None,
    scope_input: Callable[[str, Mapping[str, CallGraph]], ClusterScopeInput] = _unseeded_scope,
) -> ClusterScopeResult:
    """Build and cache the complete deterministic hierarchy from full or seeded leaf clusters."""
    root_results = (
        build_all_cluster_results(static_analysis) if root_leaf_clusters is None else dict(root_leaf_clusters)
    )

    def hierarchy_input(scope_id: str, graphs: Mapping[str, CallGraph]) -> ClusterScopeInput:
        provided = scope_input(scope_id, graphs)
        if scope_id != "root":
            return provided
        return ClusterScopeInput(
            leaf_clusters_by_language=root_results,
            previous_owner=provided.previous_owner,
            previous_member_owner=provided.previous_member_owner,
            reserved_group_ids=provided.reserved_group_ids,
        )

    hierarchy = ClusteringService().cluster_hierarchy(
        static_analysis.available_cfgs(),
        max_depth,
        hierarchy_input,
    )
    _record_child_scopes(static_analysis, hierarchy)
    return hierarchy


def build_all_cluster_results(static_analysis: StaticAnalysisResults) -> dict[str, ClusterResult]:
    """Cluster every detected language in one shared cluster-ID namespace."""
    cluster_service = ClusteringService()
    cluster_results: dict[str, ClusterResult] = {}
    offset = 0
    for language in static_analysis.get_languages():
        result = cluster_service.cluster(static_analysis.get_cfg(language))
        if offset:
            result = reindex_cluster_result(result, offset)
            logger.info(
                "[Cluster] %s: offset IDs by +%d (%d clusters)",
                language,
                offset,
                len(result.clusters),
            )
        cluster_results[str(language)] = result
        offset += max(result.clusters, default=0) + 1

    _sync_cluster_cache(static_analysis, cluster_results)
    return cluster_results


def _sync_cluster_cache(static_analysis: StaticAnalysisResults, cluster_results: dict[str, ClusterResult]) -> None:
    """Store each language's final partition on its cluster cache."""
    for language, result in cluster_results.items():
        try:
            static_analysis.get_clusters(Language(language)).adopt(result)
        except ValueError:
            logger.warning("Could not sync cluster cache for unknown language %s", language)


def _record_child_scopes(static_analysis: StaticAnalysisResults, scope: ClusterScopeResult) -> None:
    """Store retained child partitions under their parent component IDs."""
    for group in scope.groups:
        if group.children is None:
            continue
        for language, partition in group.children.leaf_clusters_by_language.items():
            static_analysis.get_clusters(Language(language)).record_scope(partition, group.group_id)
        _record_child_scopes(static_analysis, group.children)
