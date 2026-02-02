"""
Incremental analysis orchestrator for coordinating incremental static analysis workflows.

This module provides the main orchestration logic for incremental analysis,
coordinating between cache management, git diff analysis, and LSP client operations.
"""

import logging
from pathlib import Path
from typing import TYPE_CHECKING

from static_analyzer.analysis_cache import AnalysisCacheManager
from static_analyzer.cluster_change_analyzer import (
    ClusterChangeAnalyzer,
    ClusterChangeResult,
    ChangeClassification,
    analyze_cluster_changes_for_languages,
    get_overall_classification,
)
from static_analyzer.cluster_helpers import build_all_cluster_results
from static_analyzer.git_diff_analyzer import GitDiffAnalyzer
from static_analyzer.graph import CallGraph, ClusterResult

if TYPE_CHECKING:
    from static_analyzer.lsp_client.client import LSPClient

logger = logging.getLogger(__name__)


class IncrementalAnalysisOrchestrator:
    """
    Orchestrates incremental static analysis workflows.

    Coordinates between cache management, git diff analysis, and LSP client
    to provide efficient incremental analysis capabilities.
    """

    def __init__(self):
        """Initialize the incremental analysis orchestrator."""
        self.cache_manager = AnalysisCacheManager()
        self.cluster_analyzer = ClusterChangeAnalyzer()

    def run_incremental_analysis(
        self, lsp_client: "LSPClient", cache_path: Path, analyze_cluster_changes: bool = True
    ) -> dict:
        """
        Run incremental static analysis using cached results when possible.

        Args:
            lsp_client: LSP client instance for performing analysis
            cache_path: Path to the cache file
            analyze_cluster_changes: Whether to analyze and classify cluster changes

        Returns:
            Dictionary containing complete analysis results with optional cluster change info:
            - 'analysis_result': The merged analysis results
            - 'cluster_change_result': ClusterChangeResult if analyze_cluster_changes=True
            - 'change_classification': ChangeClassification if analyze_cluster_changes=True
        """
        try:
            # Initialize git diff analyzer
            git_analyzer = GitDiffAnalyzer(lsp_client.project_path)
            current_commit = git_analyzer.get_current_commit()
            logger.info(f"Current commit: {current_commit}")

            # Try to load existing cache with cluster results
            cache_result = self.cache_manager.load_cache_with_clusters(cache_path)

            if cache_result is None:
                # No cache exists - perform full analysis
                logger.info("No cache found, performing full analysis")
                analysis_result = self._perform_full_analysis_and_cache(lsp_client, cache_path, current_commit)
                if analyze_cluster_changes:
                    return {
                        "analysis_result": analysis_result,
                        "cluster_change_result": None,
                        "change_classification": ChangeClassification.BIG,  # Full analysis = BIG change
                    }
                return analysis_result

            cached_analysis, cached_cluster_results, cached_commit, cached_iteration = cache_result
            logger.info(f"Cache loaded successfully: commit {cached_commit}, iteration {cached_iteration}")

            # Log cache statistics
            cached_call_graph = cached_analysis.get("call_graph", CallGraph())
            cached_references = cached_analysis.get("references", [])
            cached_classes = cached_analysis.get("class_hierarchies", {})
            cached_packages = cached_analysis.get("package_relations", {})
            cached_files = cached_analysis.get("source_files", [])

            logger.info(
                f"Cached analysis contains: {len(cached_files)} files, "
                f"{len(cached_references)} references, {len(cached_classes)} classes, "
                f"{len(cached_packages)} packages, {len(cached_call_graph.nodes)} call graph nodes, "
                f"{len(cached_call_graph.edges)} edges"
            )

            # Check if we need incremental update
            if cached_commit == current_commit and not git_analyzer.has_uncommitted_changes():
                logger.info("No changes detected, using cached results")
                if analyze_cluster_changes:
                    return {
                        "analysis_result": cached_analysis,
                        "cluster_change_result": None,
                        "change_classification": ChangeClassification.SMALL,  # No changes = SMALL
                    }
                return cached_analysis

            # Check for uncommitted changes
            has_uncommitted = git_analyzer.has_uncommitted_changes()
            if has_uncommitted:
                logger.info("Uncommitted changes detected in working directory")

            # Perform incremental update
            logger.info(f"Performing incremental update from commit {cached_commit} to {current_commit}")
            return self._perform_incremental_update(
                lsp_client,
                cache_path,
                cached_analysis,
                cached_cluster_results,
                cached_commit,
                cached_iteration,
                current_commit,
                git_analyzer,
                analyze_cluster_changes,
            )

        except Exception as e:
            logger.error(f"Incremental analysis failed: {e}")
            logger.info("Falling back to full analysis")
            # Try to get current commit for fallback, use placeholder if that fails too
            try:
                git_analyzer = GitDiffAnalyzer(lsp_client.project_path)
                current_commit = git_analyzer.get_current_commit()
            except Exception:
                current_commit = "unknown"
                logger.warning("Could not determine current commit for fallback analysis")
            return self._perform_full_analysis_and_cache(
                lsp_client, cache_path, current_commit, analyze_cluster_changes
            )

    def _should_use_cache(self, cache_path: Path) -> bool:
        """
        Determine if cache should be used.

        Args:
            cache_path: Path to the cache file

        Returns:
            True if cache exists and is valid, False otherwise
        """
        if not cache_path.exists():
            return False

        # Try to load cache to validate it
        cache_result = self.cache_manager.load_cache(cache_path)
        return cache_result is not None

    def _perform_full_analysis_and_cache(
        self, lsp_client: "LSPClient", cache_path: Path, commit_hash: str, analyze_clusters: bool = True
    ) -> dict:
        """
        Perform full analysis and save results to cache.

        Args:
            lsp_client: LSP client for analysis
            cache_path: Path to save cache
            commit_hash: Current commit hash
            analyze_clusters: Whether to compute and cache cluster results

        Returns:
            Complete analysis results
        """
        logger.info("Starting full static analysis")

        # Get source files count for progress tracking
        try:
            src_files = lsp_client._get_source_files()
            filtered_files = lsp_client.filter_src_files(src_files)
            logger.info(f"Will analyze {len(filtered_files)} source files")
        except Exception as e:
            logger.debug(f"Could not get source file count for logging: {e}")

        # Perform full analysis
        analysis_result = lsp_client.build_static_analysis()

        # Log analysis statistics
        call_graph = analysis_result.get("call_graph", CallGraph())
        references = analysis_result.get("references", [])
        classes = analysis_result.get("class_hierarchies", {})
        packages = analysis_result.get("package_relations", {})
        source_files = analysis_result.get("source_files", [])

        logger.info(
            f"Full analysis complete: {len(source_files)} files processed, "
            f"{len(references)} references, {len(classes)} classes, "
            f"{len(packages)} packages, {len(call_graph.nodes)} call graph nodes, "
            f"{len(call_graph.edges)} edges"
        )

        # Compute cluster results if requested
        cluster_results = None
        if analyze_clusters:
            logger.info("Computing cluster results for cache...")
            cluster_results = self._compute_cluster_results(analysis_result)

        # Save to cache
        try:
            logger.info(f"Saving analysis results to cache: {cache_path}")
            if cluster_results:
                self.cache_manager.save_cache_with_clusters(
                    cache_path=cache_path,
                    analysis_result=analysis_result,
                    cluster_results=cluster_results,
                    commit_hash=commit_hash,
                    iteration_id=1,
                )
            else:
                self.cache_manager.save_cache(
                    cache_path=cache_path, analysis_result=analysis_result, commit_hash=commit_hash, iteration_id=1
                )
            logger.info("Full analysis complete and cached successfully")
        except Exception as e:
            logger.warning(f"Failed to save cache after full analysis: {e}")

        return analysis_result

    def _perform_incremental_update(
        self,
        lsp_client: "LSPClient",
        cache_path: Path,
        cached_analysis: dict,
        cached_cluster_results: dict[str, ClusterResult],
        cached_commit: str,
        cached_iteration: int,
        current_commit: str,
        git_analyzer: GitDiffAnalyzer,
        analyze_cluster_changes: bool = True,
    ) -> dict:
        """
        Perform incremental analysis update.

        Args:
            lsp_client: LSP client for analysis
            cache_path: Path to cache file
            cached_analysis: Previously cached analysis results
            cached_cluster_results: Previously cached cluster results
            cached_commit: Commit hash of cached analysis
            cached_iteration: Iteration ID of cached analysis
            current_commit: Current commit hash
            git_analyzer: Git diff analyzer instance
            analyze_cluster_changes: Whether to analyze and classify cluster changes

        Returns:
            Dictionary containing analysis results and optionally cluster change info
        """
        try:
            # Get changed files and separate by existence
            logger.info(f"Identifying changed files between {cached_commit} and {current_commit}")
            changed_files = git_analyzer.get_changed_files(cached_commit)

            if not changed_files:
                logger.info("No files changed, using cached results")
                if analyze_cluster_changes:
                    return {
                        "analysis_result": cached_analysis,
                        "cluster_change_result": None,
                        "change_classification": ChangeClassification.SMALL,
                    }
                return cached_analysis

            # Separate changed files into existing and deleted sets
            existing_files = {f for f in changed_files if f.exists()}
            deleted_files = {f for f in changed_files if not f.exists()}

            logger.info(
                f"Found {len(changed_files)} changed files: {len(existing_files)} existing, {len(deleted_files)} deleted"
            )
            for file_path in sorted(changed_files):
                logger.debug(f"Changed file: {file_path}")
            for df in deleted_files:
                logger.debug(f"Deleted file: {df}")

            # Remove data for changed files from cache
            logger.info(f"Invalidating {len(changed_files)} changed files from cache")
            updated_cache = self.cache_manager.invalidate_files(cached_analysis, changed_files)

            # Reanalyze changed files
            logger.info(f"Reanalyzing {len(existing_files)} existing changed files")
            new_analysis = lsp_client._analyze_specific_files(existing_files)

            # Merge results
            logger.info("Merging new analysis with cached results")
            merged_analysis = self.cache_manager.merge_results(updated_cache, new_analysis)

            # Filter merged results to only include files that exist in current commit
            # This ensures deleted files are properly removed
            existing_files = {f for f in merged_analysis.get("source_files", []) if f.exists()}
            existing_file_strs = {str(f) for f in existing_files}

            # Filter source_files
            merged_analysis["source_files"] = list(existing_files)

            # Filter references to only include existing files
            merged_analysis["references"] = [
                ref for ref in merged_analysis.get("references", []) if ref.file_path in existing_file_strs
            ]

            # Filter call graph nodes and edges
            merged_cg = merged_analysis.get("call_graph", CallGraph())
            filtered_cg = CallGraph()
            for name, node in merged_cg.nodes.items():
                if node.file_path in existing_file_strs:
                    filtered_cg.add_node(node)
            for edge in merged_cg.edges:
                src, dst = edge.get_source(), edge.get_destination()
                if src in filtered_cg.nodes and dst in filtered_cg.nodes:
                    try:
                        filtered_cg.add_edge(src, dst)
                    except ValueError:
                        pass
            merged_analysis["call_graph"] = filtered_cg

            # Filter class hierarchies
            merged_analysis["class_hierarchies"] = {
                name: info
                for name, info in merged_analysis.get("class_hierarchies", {}).items()
                if info.get("file_path") in existing_file_strs
            }

            # Filter package relations
            filtered_packages = {}
            for pkg_name, pkg_info in merged_analysis.get("package_relations", {}).items():
                pkg_files = pkg_info.get("files", [])
                existing_pkg_files = [f for f in pkg_files if f in existing_file_strs]
                if existing_pkg_files:
                    filtered_packages[pkg_name] = pkg_info.copy()
                    filtered_packages[pkg_name]["files"] = existing_pkg_files
            merged_analysis["package_relations"] = filtered_packages

            # Log results summary
            cg = merged_analysis.get("call_graph", CallGraph())
            logger.info(
                f"Incremental update complete: {len(merged_analysis.get('source_files', []))} files, "
                f"{len(merged_analysis.get('references', []))} references, "
                f"{len(cg.nodes)} nodes, {len(cg.edges)} edges"
            )

            # Analyze cluster changes if requested
            cluster_change_result = None
            change_classification = ChangeClassification.SMALL
            new_cluster_results = None

            if analyze_cluster_changes:
                logger.info("Analyzing cluster changes...")
                new_cluster_results = self._compute_cluster_results(merged_analysis)
                cluster_changes = analyze_cluster_changes_for_languages(cached_cluster_results, new_cluster_results)

                # Get overall classification (worst case across all languages)
                change_classification = get_overall_classification(cluster_changes)

                # Store the primary language result for detailed reporting
                primary_lang = list(cluster_changes.keys())[0] if cluster_changes else ""
                cluster_change_result = cluster_changes.get(primary_lang)

                logger.info(f"Cluster change classification: {change_classification.value}")

            # Save updated cache with cluster results
            try:
                logger.info(f"Saving updated cache to: {cache_path}")
                if new_cluster_results:
                    self.cache_manager.save_cache_with_clusters(
                        cache_path=cache_path,
                        analysis_result=merged_analysis,
                        cluster_results=new_cluster_results,
                        commit_hash=current_commit,
                        iteration_id=cached_iteration + 1,
                    )
                else:
                    self.cache_manager.save_cache(
                        cache_path=cache_path,
                        analysis_result=merged_analysis,
                        commit_hash=current_commit,
                        iteration_id=cached_iteration + 1,
                    )
                logger.info(f"Incremental analysis complete, cache updated (iteration {cached_iteration + 1})")
            except Exception as e:
                logger.warning(f"Failed to save updated cache: {e}")

            if analyze_cluster_changes:
                return {
                    "analysis_result": merged_analysis,
                    "cluster_change_result": cluster_change_result,
                    "change_classification": change_classification,
                }
            return merged_analysis

        except Exception as e:
            logger.error(f"Incremental update failed: {e}")
            raise

    def _compute_cluster_results(self, analysis_result: dict) -> dict[str, ClusterResult]:
        """
        Compute cluster results from analysis result.

        Args:
            analysis_result: Dictionary containing analysis results with call_graph

        Returns:
            Dictionary mapping language -> ClusterResult
        """
        cluster_results = {}
        call_graph = analysis_result.get("call_graph", CallGraph())

        if call_graph.nodes:
            # For now, we treat the entire call graph as a single language (python)
            # In the future, this could be extended to support multiple languages
            cluster_result = call_graph.cluster()
            cluster_results[call_graph.language] = cluster_result
            logger.info(
                f"Computed clusters for {call_graph.language}: "
                f"{len(cluster_result.get_cluster_ids())} clusters, "
                f"strategy={cluster_result.strategy}"
            )

        return cluster_results
