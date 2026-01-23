"""
Helper functions for working with CFG cluster analysis.

This module provides common patterns for cluster operations to reduce code duplication
across agents and other components that work with static analysis cluster results.
"""

from typing import Dict

from static_analyzer.analysis_result import StaticAnalysisResults
from static_analyzer.graph import ClusterResult


def build_cluster_results_for_languages(
    static_analysis: StaticAnalysisResults, languages: list[str]
) -> Dict[str, ClusterResult]:
    """
    Build cluster results for specified languages.

    Args:
        static_analysis: Static analysis results containing CFG data
        languages: List of language names to build cluster results for

    Returns:
        Dictionary mapping language name -> ClusterResult
    """
    cluster_results = {}
    for lang in languages:
        cfg = static_analysis.get_cfg(lang)
        cluster_results[lang] = cfg.cluster()
    return cluster_results


def build_all_cluster_results(static_analysis: StaticAnalysisResults) -> Dict[str, ClusterResult]:
    """
    Build cluster results for all detected languages in the static analysis.

    Args:
        static_analysis: Static analysis results containing CFG data

    Returns:
        Dictionary mapping language name -> ClusterResult
    """
    languages = static_analysis.get_languages()
    return build_cluster_results_for_languages(static_analysis, languages)


def get_all_cluster_ids(cluster_results: Dict[str, ClusterResult]) -> set[int]:
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


def get_files_for_cluster_ids(cluster_ids: list[int], cluster_results: Dict[str, ClusterResult]) -> set[str]:
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
