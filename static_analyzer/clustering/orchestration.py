"""Root-scope clustering orchestration and cache synchronization."""

import logging
from collections.abc import Mapping

from static_analyzer.analysis_result import StaticAnalysisResults
from static_analyzer.cfg import CallGraph
from static_analyzer.clustering.grouping import reindex_cluster_result
from static_analyzer.clustering.models import ClusterResult, ClusterScopeInput, ClusterScopeResult
from static_analyzer.clustering.service import ClusteringService
from static_analyzer.config import Language

logger = logging.getLogger(__name__)


def build_clustering_hierarchy(static_analysis: StaticAnalysisResults, max_depth: int) -> ClusterScopeResult:
    """Build and cache the complete deterministic hierarchy for a full analysis."""
    root_partitions = build_all_cluster_results(static_analysis)

    def scope_input(scope_id: str, _graphs: Mapping[str, CallGraph]) -> ClusterScopeInput:
        if scope_id == "root":
            return ClusterScopeInput(leaf_clusters_by_language=root_partitions)
        return ClusterScopeInput()

    hierarchy = ClusteringService().cluster_hierarchy(
        static_analysis.available_cfgs(),
        max_depth,
        scope_input,
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
