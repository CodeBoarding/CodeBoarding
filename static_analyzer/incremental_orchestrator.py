"""
Incremental analysis orchestrator for coordinating incremental static analysis workflows.

This module provides the main orchestration logic for incremental analysis,
coordinating between cache management, git diff analysis, and LSP client operations.
"""

import logging
from pathlib import Path
from typing import TYPE_CHECKING

from static_analyzer.analysis_cache import AnalysisCacheManager
from static_analyzer.git_diff_analyzer import GitDiffAnalyzer
from static_analyzer.graph import CallGraph

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

    def run_incremental_analysis(self, lsp_client: "LSPClient", cache_path: Path) -> dict:
        """
        Run incremental static analysis using cached results when possible.

        Args:
            lsp_client: LSP client instance for performing analysis
            cache_path: Path to the cache file

        Returns:
            Dictionary containing complete analysis results

        The workflow:
        1. Check if cache exists and is valid
        2. If no cache, perform full analysis and save cache
        3. If cache exists, identify changed files using git diff
        4. Remove data for changed files from cache
        5. Reanalyze only changed files
        6. Merge new results with cached results
        7. Save updated cache
        """
        try:
            # Initialize git diff analyzer
            git_analyzer = GitDiffAnalyzer(lsp_client.project_path)
            current_commit = git_analyzer.get_current_commit()
            logger.info(f"Current commit: {current_commit}")

            # Try to load existing cache
            cache_result = self.cache_manager.load_cache(cache_path)

            if cache_result is None:
                # No cache exists - perform full analysis
                logger.info("No cache found, performing full analysis")
                return self._perform_full_analysis_and_cache(lsp_client, cache_path, current_commit)

            cached_analysis, cached_commit, cached_iteration = cache_result
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
                return cached_analysis

            # Check for uncommitted changes
            has_uncommitted = git_analyzer.has_uncommitted_changes()
            if has_uncommitted:
                logger.info("Uncommitted changes detected in working directory")

            # Perform incremental update
            logger.info(f"Performing incremental update from commit {cached_commit} to {current_commit}")
            return self._perform_incremental_update(
                lsp_client, cache_path, cached_analysis, cached_commit, cached_iteration, current_commit, git_analyzer
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
            return self._perform_full_analysis_and_cache(lsp_client, cache_path, current_commit)

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

    def _perform_full_analysis_and_cache(self, lsp_client: "LSPClient", cache_path: Path, commit_hash: str) -> dict:
        """
        Perform full analysis and save results to cache.

        Args:
            lsp_client: LSP client for analysis
            cache_path: Path to save cache
            commit_hash: Current commit hash

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

        # Save to cache
        try:
            logger.info(f"Saving analysis results to cache: {cache_path}")
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
        cached_commit: str,
        cached_iteration: int,
        current_commit: str,
        git_analyzer: GitDiffAnalyzer,
    ) -> dict:
        """
        Perform incremental analysis update.

        Args:
            lsp_client: LSP client for analysis
            cache_path: Path to cache file
            cached_analysis: Previously cached analysis results
            cached_commit: Commit hash of cached analysis
            cached_iteration: Iteration ID of cached analysis
            current_commit: Current commit hash
            git_analyzer: Git diff analyzer instance

        Returns:
            Updated analysis results
        """
        try:
            # Get changed files
            logger.info(f"Identifying changed files between {cached_commit} and {current_commit}")
            changed_files = git_analyzer.get_changed_files(cached_commit)

            if not changed_files:
                logger.info("No files changed, using cached results")
                return cached_analysis

            logger.info(f"Found {len(changed_files)} changed files")
            for file_path in sorted(changed_files):
                logger.debug(f"Changed file: {file_path}")

            # Log cache invalidation progress
            logger.info("Invalidating cached data for changed files")
            # Debug: log which changed files exist
            existing_changed = [f for f in changed_files if f.exists()]
            deleted_changed = [f for f in changed_files if not f.exists()]
            logger.info(f"Changed files breakdown: {len(existing_changed)} existing, {len(deleted_changed)} deleted")
            for df in deleted_changed:
                logger.debug(f"Deleted file: {df}")

            cached_call_graph = cached_analysis.get("call_graph", CallGraph())
            cached_references = cached_analysis.get("references", [])
            cached_classes = cached_analysis.get("class_hierarchies", {})
            cached_packages = cached_analysis.get("package_relations", {})

            logger.debug(
                f"Before invalidation: {len(cached_references)} references, "
                f"{len(cached_classes)} classes, {len(cached_packages)} packages, "
                f"{len(cached_call_graph.nodes)} call graph nodes, {len(cached_call_graph.edges)} edges"
            )

            # Remove data for changed files from cache
            updated_cache = self.cache_manager.invalidate_files(cached_analysis, changed_files)

            # Log post-invalidation statistics
            updated_call_graph = updated_cache.get("call_graph", CallGraph())
            updated_references = updated_cache.get("references", [])
            updated_classes = updated_cache.get("class_hierarchies", {})
            updated_packages = updated_cache.get("package_relations", {})

            logger.info(
                f"After invalidation: {len(updated_references)} references, "
                f"{len(updated_classes)} classes, {len(updated_packages)} packages, "
                f"{len(updated_call_graph.nodes)} call graph nodes, {len(updated_call_graph.edges)} edges"
            )

            # Analyze only changed files using LSP client method
            logger.info("Reanalyzing changed files")
            new_analysis = lsp_client._analyze_specific_files(changed_files)

            # Log new analysis statistics
            new_call_graph = new_analysis.get("call_graph", CallGraph())
            new_references = new_analysis.get("references", [])
            new_classes = new_analysis.get("class_hierarchies", {})
            new_packages = new_analysis.get("package_relations", {})
            new_files = new_analysis.get("source_files", [])

            logger.info(
                f"New analysis results: {len(new_files)} files, "
                f"{len(new_references)} references, {len(new_classes)} classes, "
                f"{len(new_packages)} packages, {len(new_call_graph.nodes)} call graph nodes, "
                f"{len(new_call_graph.edges)} edges"
            )

            # Merge results
            logger.info("Merging new analysis with cached results")
            merged_analysis = self.cache_manager.merge_results(updated_cache, new_analysis)

            # Log final merged statistics
            merged_call_graph = merged_analysis.get("call_graph", CallGraph())
            merged_references = merged_analysis.get("references", [])
            merged_classes = merged_analysis.get("class_hierarchies", {})
            merged_packages = merged_analysis.get("package_relations", {})
            merged_files = merged_analysis.get("source_files", [])

            logger.info(
                f"Final merged results: {len(merged_files)} files, "
                f"{len(merged_references)} references, {len(merged_classes)} classes, "
                f"{len(merged_packages)} packages, {len(merged_call_graph.nodes)} call graph nodes, "
                f"{len(merged_call_graph.edges)} edges"
            )

            # Save updated cache
            try:
                logger.info(f"Saving updated cache to: {cache_path}")
                self.cache_manager.save_cache(
                    cache_path=cache_path,
                    analysis_result=merged_analysis,
                    commit_hash=current_commit,
                    iteration_id=cached_iteration + 1,
                )
                logger.info(f"Incremental analysis complete, cache updated (iteration {cached_iteration + 1})")
            except Exception as e:
                logger.warning(f"Failed to save updated cache: {e}")

            return merged_analysis

        except Exception as e:
            logger.error(f"Incremental update failed: {e}")
            raise
