"""Root-scope clustering orchestration and cache synchronization."""

import logging

from static_analyzer.analysis_result import StaticAnalysisResults
from static_analyzer.clustering.grouping import reindex_cluster_result
from static_analyzer.clustering.models import ClusterResult
from static_analyzer.clustering.service import ClusteringService
from static_analyzer.constants import Language

logger = logging.getLogger(__name__)


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
