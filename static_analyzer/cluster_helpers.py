"""Helpers for ProgramGraph clustering and downstream cluster ID handling."""

import logging
from collections import defaultdict
from typing import TypeVar

from static_analyzer.analysis_result import StaticAnalysisResults
from static_analyzer.clustering import ClusterResult
from static_analyzer.constants import Language
from static_analyzer.infomap_clustering import HierarchicalInfomapClusterer

logger = logging.getLogger(__name__)
NodeId = TypeVar("NodeId")


def build_cluster_results_for_languages(
    static_analysis: StaticAnalysisResults, languages: list[Language]
) -> dict[str, ClusterResult]:
    """
    Build cluster results for specified languages.

    Args:
        static_analysis: Static analysis results containing CFG data
        languages: List of language names to build cluster results for

    Returns:
        Dictionary mapping language name -> ClusterResult
    """
    cluster_results: dict[str, ClusterResult] = {}
    clusterer = HierarchicalInfomapClusterer()
    for lang in languages:
        graph = static_analysis.get_program_graph(lang)
        cluster_results[str(lang)] = clusterer.cluster(graph)
    return cluster_results


def build_all_cluster_results(static_analysis: StaticAnalysisResults) -> dict[str, ClusterResult]:
    """
    Build cluster results for all detected languages in the static analysis.

    Hierarchical Infomap decides module granularity. This function never
    hyperclusters its output; it only gives languages disjoint ID ranges.

    Args:
        static_analysis: Static analysis results containing CFG data

    Returns:
        Dictionary mapping language name -> ClusterResult
    """
    languages = static_analysis.get_languages()
    cluster_results = build_cluster_results_for_languages(static_analysis, languages)

    if len(cluster_results) > 1:
        reindex_cross_language_clusters(cluster_results)

    return cluster_results


def reindex_cross_language_clusters(cluster_results: dict[str, ClusterResult]) -> None:
    """Give each language a deterministic, disjoint cluster-ID range."""
    offset = 0
    for lang in sorted(cluster_results):
        result = cluster_results[lang]
        if offset:
            cluster_results[lang] = reindex_cluster_result(result, offset)
        offset = max(cluster_results[lang].clusters, default=offset)


def get_all_cluster_ids(cluster_results: dict[str, ClusterResult]) -> set[int]:
    """
    Get all cluster IDs from cluster results across all languages.

    Args:
        cluster_results: Dictionary mapping language -> ClusterResult

    Returns:
        Set of all cluster IDs found across all languages
    """
    cluster_ids = set()
    for cluster_result in cluster_results.values():
        cluster_ids.update(cluster_result.get_cluster_ids())
    return cluster_ids


def get_files_for_cluster_ids(cluster_ids: list[int], cluster_results: dict[str, ClusterResult]) -> set[str]:
    """
    Get all files that belong to the specified cluster IDs across all languages.

    Args:
        cluster_ids: List of cluster IDs to get files for
        cluster_results: Dictionary mapping language -> ClusterResult

    Returns:
        Set of file paths belonging to the specified clusters
    """
    files: set[str] = set()
    for cluster_result in cluster_results.values():
        for cluster_id in cluster_ids:
            files.update(cluster_result.get_files_for_cluster(cluster_id))
    return files


def reindex_cluster_result(cluster_result: ClusterResult, offset: int) -> ClusterResult:
    """Re-index all cluster IDs in a ClusterResult by adding an offset.

    Args:
        cluster_result: Original ClusterResult
        offset: Integer to add to every cluster ID

    Returns:
        New ClusterResult with shifted IDs
    """
    new_clusters: dict[int, set[str]] = {}
    new_cluster_to_files: dict[int, set[str]] = {}
    new_file_to_clusters: dict[str, set[int]] = defaultdict(set)

    for old_id, nodes in cluster_result.clusters.items():
        new_id = old_id + offset
        new_clusters[new_id] = nodes
        if old_id in cluster_result.cluster_to_files:
            new_cluster_to_files[new_id] = cluster_result.cluster_to_files[old_id]

    for file_path, old_ids in cluster_result.file_to_clusters.items():
        new_file_to_clusters[file_path] = {old_id + offset for old_id in old_ids}

    return ClusterResult(
        clusters=new_clusters,
        cluster_to_files=new_cluster_to_files,
        file_to_clusters=dict(new_file_to_clusters),
        strategy=cluster_result.strategy,
    )
