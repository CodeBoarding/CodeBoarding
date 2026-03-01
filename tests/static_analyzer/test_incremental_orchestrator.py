"""Tests for IncrementalAnalysisOrchestrator."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

from static_analyzer.cluster_change_analyzer import (
    ChangeClassification,
    ClusterChangeAnalyzer,
    ClusterChangeResult,
)
from static_analyzer.graph import CallGraph, ClusterResult
from static_analyzer.node import Node
from static_analyzer.incremental_orchestrator import IncrementalAnalysisOrchestrator


# Helper functions to create test data
def create_test_node(name: str = "test.module.func", file_path: str = "/test/file.py") -> Node:
    """Create a test Node with sensible defaults."""
    return Node(
        fully_qualified_name=name,
        node_type=12,  # Function type
        file_path=file_path,
        line_start=1,
        line_end=10,
    )


def create_test_call_graph(num_nodes: int = 3) -> CallGraph:
    """Create a test CallGraph with specified number of nodes."""
    graph = CallGraph()
    for i in range(num_nodes):
        node = create_test_node(f"test.func{i}", f"/test/file{i}.py")
        graph.add_node(node)
    # Add some edges
    for i in range(num_nodes - 1):
        graph.add_edge(f"test.func{i}", f"test.func{i+1}")
    return graph


def create_test_analysis_result(num_nodes: int = 3) -> dict:
    """Create a valid analysis result dict."""
    call_graph = create_test_call_graph(num_nodes)
    source_files = [Path(f"/test/file{i}.py") for i in range(num_nodes)]

    return {
        "call_graph": call_graph,
        "class_hierarchies": {},
        "package_relations": {},
        "references": [],
        "source_files": source_files,
    }


def create_test_cluster_result(num_clusters: int = 2) -> dict[str, ClusterResult]:
    """Create test cluster results."""
    clusters = {}
    file_to_clusters: dict[str, set[int]] = {}
    cluster_to_files = {}

    for cluster_id in range(num_clusters):
        node_names = {f"test.func{cluster_id}_0", f"test.func{cluster_id}_1"}
        file_paths = {f"/test/file{cluster_id}.py"}

        clusters[cluster_id] = node_names
        cluster_to_files[cluster_id] = file_paths

        for file_path in file_paths:
            if file_path not in file_to_clusters:
                file_to_clusters[file_path] = set()
            file_to_clusters[file_path].add(cluster_id)

    cluster_result = ClusterResult(
        clusters=clusters,
        file_to_clusters=file_to_clusters,
        cluster_to_files=cluster_to_files,
        strategy="test_strategy",
    )

    return {"python": cluster_result}


def create_mock_lsp_client() -> Mock:
    """Create a mock LSP client with common methods."""
    mock_client = Mock()
    mock_client.project_path = Path("/test/project")
    mock_client.build_static_analysis.return_value = create_test_analysis_result()
    mock_client._get_source_files.return_value = [Path("/test/file0.py"), Path("/test/file1.py")]
    mock_client.filter_src_files.return_value = [Path("/test/file0.py"), Path("/test/file1.py")]
    mock_client._analyze_specific_files.return_value = create_test_analysis_result(2)
    return mock_client


class TestIncrementalAnalysisOrchestratorInit(unittest.TestCase):
    """Tests for IncrementalAnalysisOrchestrator initialization."""

    def test_init_creates_cache_manager(self):
        """Test that __init__ creates an AnalysisCacheManager."""
        orchestrator = IncrementalAnalysisOrchestrator()

        self.assertIsNotNone(orchestrator.cache_manager)
        self.assertEqual(orchestrator.cache_manager.__class__.__name__, "AnalysisCacheManager")

    def test_init_creates_cluster_analyzer(self):
        """Test that __init__ creates a ClusterChangeAnalyzer."""
        orchestrator = IncrementalAnalysisOrchestrator()

        self.assertIsNotNone(orchestrator.cluster_analyzer)
        self.assertIsInstance(orchestrator.cluster_analyzer, ClusterChangeAnalyzer)


class TestShouldUseCache(unittest.TestCase):
    """Tests for _should_use_cache method."""

    def setUp(self):
        self.orchestrator = IncrementalAnalysisOrchestrator()
        self.temp_dir = tempfile.mkdtemp()
        self.cache_path = Path(self.temp_dir) / "test_cache.json"

    def test_returns_false_when_cache_doesnt_exist(self):
        """_should_use_cache returns False when cache file doesn't exist."""
        result = self.orchestrator._should_use_cache(self.cache_path)

        self.assertFalse(result)

    def test_returns_false_when_cache_is_invalid(self):
        """_should_use_cache returns False when cache is invalid."""
        # Create an invalid cache file
        self.cache_path.write_text("invalid json content")

        result = self.orchestrator._should_use_cache(self.cache_path)

        self.assertFalse(result)

    def test_returns_true_when_cache_is_valid(self):
        """_should_use_cache returns True when cache is valid."""
        # Mock the cache manager to return valid cache
        with patch.object(self.orchestrator.cache_manager, "load_cache") as mock_load:
            mock_load.return_value = (create_test_analysis_result(), "abc123", 1)

            # Create the file so it exists
            self.cache_path.touch()

            result = self.orchestrator._should_use_cache(self.cache_path)

            self.assertTrue(result)


class TestRunIncrementalAnalysisNoCache(unittest.TestCase):
    """Tests for run_incremental_analysis when no cache exists."""

    def setUp(self):
        self.orchestrator = IncrementalAnalysisOrchestrator()
        self.mock_lsp_client = create_mock_lsp_client()
        self.cache_path = Path("/test/cache.json")

    @patch("static_analyzer.incremental_orchestrator.GitDiffAnalyzer")
    def test_no_cache_performs_full_analysis(self, mock_git_analyzer_class):
        """When no cache exists, performs full analysis."""
        # Setup mocks
        mock_git_analyzer = Mock()
        mock_git_analyzer.get_current_commit.return_value = "commit123"
        mock_git_analyzer_class.return_value = mock_git_analyzer

        with patch.object(self.orchestrator.cache_manager, "load_cache_with_clusters") as mock_load:
            mock_load.return_value = None  # No cache

            with patch.object(self.orchestrator, "_perform_full_analysis_and_cache") as mock_full:
                expected_result = create_test_analysis_result()
                mock_full.return_value = expected_result

                result = self.orchestrator.run_incremental_analysis(
                    self.mock_lsp_client,
                    self.cache_path,
                    analyze_cluster_changes=False,
                )

                # Verify full analysis was called
                mock_full.assert_called_once_with(
                    self.mock_lsp_client,
                    self.cache_path,
                    "commit123",
                )
                self.assertEqual(result, expected_result)

    @patch("static_analyzer.incremental_orchestrator.GitDiffAnalyzer")
    def test_no_cache_returns_big_classification(self, mock_git_analyzer_class):
        """When no cache exists and analyze_cluster_changes=True, returns BIG classification."""
        # Setup mocks
        mock_git_analyzer = Mock()
        mock_git_analyzer.get_current_commit.return_value = "commit123"
        mock_git_analyzer_class.return_value = mock_git_analyzer

        with patch.object(self.orchestrator.cache_manager, "load_cache_with_clusters") as mock_load:
            mock_load.return_value = None  # No cache

            with patch.object(self.orchestrator, "_perform_full_analysis_and_cache") as mock_full:
                expected_result = create_test_analysis_result()
                mock_full.return_value = expected_result

                result = self.orchestrator.run_incremental_analysis(
                    self.mock_lsp_client,
                    self.cache_path,
                    analyze_cluster_changes=True,
                )

                # Verify result structure
                self.assertIsInstance(result, dict)
                self.assertIn("analysis_result", result)
                self.assertIn("cluster_change_result", result)
                self.assertIn("change_classification", result)
                self.assertEqual(result["analysis_result"], expected_result)
                self.assertIsNone(result["cluster_change_result"])
                self.assertEqual(result["change_classification"], ChangeClassification.BIG)


class TestRunIncrementalAnalysisCachedNoChanges(unittest.TestCase):
    """Tests for run_incremental_analysis when cache exists and no changes."""

    def setUp(self):
        self.orchestrator = IncrementalAnalysisOrchestrator()
        self.mock_lsp_client = create_mock_lsp_client()
        self.cache_path = Path("/test/cache.json")
        self.cached_analysis = create_test_analysis_result()
        self.cached_clusters = create_test_cluster_result()
        self.commit_hash = "commit123"

    @patch("static_analyzer.incremental_orchestrator.GitDiffAnalyzer")
    def test_cached_no_changes_returns_cached(self, mock_git_analyzer_class):
        """When cache exists and no changes, returns cached results."""
        # Setup mocks
        mock_git_analyzer = Mock()
        mock_git_analyzer.get_current_commit.return_value = self.commit_hash
        mock_git_analyzer.has_uncommitted_changes.return_value = False
        mock_git_analyzer_class.return_value = mock_git_analyzer

        with patch.object(self.orchestrator.cache_manager, "load_cache_with_clusters") as mock_load:
            mock_load.return_value = (
                self.cached_analysis,
                self.cached_clusters,
                self.commit_hash,
                1,
            )

            result = self.orchestrator.run_incremental_analysis(
                self.mock_lsp_client,
                self.cache_path,
                analyze_cluster_changes=False,
            )

            # Should return cached analysis directly
            self.assertEqual(result, self.cached_analysis)

    @patch("static_analyzer.incremental_orchestrator.GitDiffAnalyzer")
    def test_cached_no_changes_returns_small_classification(self, mock_git_analyzer_class):
        """When cache exists, no changes, and analyze_cluster_changes=True, returns SMALL."""
        # Setup mocks
        mock_git_analyzer = Mock()
        mock_git_analyzer.get_current_commit.return_value = self.commit_hash
        mock_git_analyzer.has_uncommitted_changes.return_value = False
        mock_git_analyzer_class.return_value = mock_git_analyzer

        with patch.object(self.orchestrator.cache_manager, "load_cache_with_clusters") as mock_load:
            mock_load.return_value = (
                self.cached_analysis,
                self.cached_clusters,
                self.commit_hash,
                1,
            )

            result = self.orchestrator.run_incremental_analysis(
                self.mock_lsp_client,
                self.cache_path,
                analyze_cluster_changes=True,
            )

            # Verify result structure
            self.assertIsInstance(result, dict)
            self.assertIn("analysis_result", result)
            self.assertIn("cluster_change_result", result)
            self.assertIn("change_classification", result)
            self.assertEqual(result["analysis_result"], self.cached_analysis)
            self.assertIsNone(result["cluster_change_result"])
            self.assertEqual(result["change_classification"], ChangeClassification.SMALL)


class TestRunIncrementalAnalysisWithChanges(unittest.TestCase):
    """Tests for run_incremental_analysis when changes are detected."""

    def setUp(self):
        self.orchestrator = IncrementalAnalysisOrchestrator()
        self.mock_lsp_client = create_mock_lsp_client()
        self.cache_path = Path("/test/cache.json")
        self.cached_analysis = create_test_analysis_result()
        self.cached_clusters = create_test_cluster_result()
        self.cached_commit = "commit123"
        self.current_commit = "commit456"

    @patch("static_analyzer.incremental_orchestrator.GitDiffAnalyzer")
    def test_uncommitted_changes_triggers_incremental(self, mock_git_analyzer_class):
        """When uncommitted changes exist, triggers incremental update."""
        # Setup mocks
        mock_git_analyzer = Mock()
        mock_git_analyzer.get_current_commit.return_value = self.cached_commit
        mock_git_analyzer.has_uncommitted_changes.return_value = True
        mock_git_analyzer_class.return_value = mock_git_analyzer

        with patch.object(self.orchestrator.cache_manager, "load_cache_with_clusters") as mock_load:
            mock_load.return_value = (
                self.cached_analysis,
                self.cached_clusters,
                self.cached_commit,
                1,
            )

            with patch.object(self.orchestrator, "_perform_incremental_update") as mock_incremental:
                expected_result = create_test_analysis_result()
                mock_incremental.return_value = expected_result

                result = self.orchestrator.run_incremental_analysis(
                    self.mock_lsp_client,
                    self.cache_path,
                    analyze_cluster_changes=False,
                )

                # Verify incremental update was called
                mock_incremental.assert_called_once()
                self.assertEqual(result, expected_result)

    @patch("static_analyzer.incremental_orchestrator.GitDiffAnalyzer")
    def test_committed_changes_triggers_incremental(self, mock_git_analyzer_class):
        """When commit has changed, triggers incremental update."""
        # Setup mocks
        mock_git_analyzer = Mock()
        mock_git_analyzer.get_current_commit.return_value = self.current_commit
        mock_git_analyzer.has_uncommitted_changes.return_value = False
        mock_git_analyzer_class.return_value = mock_git_analyzer

        with patch.object(self.orchestrator.cache_manager, "load_cache_with_clusters") as mock_load:
            mock_load.return_value = (
                self.cached_analysis,
                self.cached_clusters,
                self.cached_commit,  # Different from current
                1,
            )

            with patch.object(self.orchestrator, "_perform_incremental_update") as mock_incremental:
                expected_result = create_test_analysis_result()
                mock_incremental.return_value = expected_result

                result = self.orchestrator.run_incremental_analysis(
                    self.mock_lsp_client,
                    self.cache_path,
                    analyze_cluster_changes=False,
                )

                # Verify incremental update was called
                mock_incremental.assert_called_once()
                call_args = mock_incremental.call_args[0]
                self.assertEqual(call_args[4], self.cached_commit)  # cached_commit (index 4)
                self.assertEqual(call_args[5], 1)  # cached_iteration (index 5)
                self.assertEqual(call_args[6], self.current_commit)  # current_commit (index 6)


class TestRunIncrementalAnalysisExceptionHandling(unittest.TestCase):
    """Tests for exception handling in run_incremental_analysis."""

    def setUp(self):
        self.orchestrator = IncrementalAnalysisOrchestrator()
        self.mock_lsp_client = create_mock_lsp_client()
        self.cache_path = Path("/test/cache.json")

    @patch("static_analyzer.incremental_orchestrator.GitDiffAnalyzer")
    def test_exception_falls_back_to_full_analysis(self, mock_git_analyzer_class):
        """When exception occurs, falls back to full analysis."""
        # Setup mocks - first call raises exception, second call succeeds
        mock_git_analyzer = Mock()
        mock_git_analyzer.get_current_commit.side_effect = [
            Exception("Git error"),  # First call fails
            "commit123",  # Second call (in fallback) succeeds
        ]
        mock_git_analyzer_class.return_value = mock_git_analyzer

        with patch.object(self.orchestrator, "_perform_full_analysis_and_cache") as mock_full:
            expected_result = create_test_analysis_result()
            mock_full.return_value = expected_result

            result = self.orchestrator.run_incremental_analysis(
                self.mock_lsp_client,
                self.cache_path,
                analyze_cluster_changes=False,
            )

            # Verify fallback to full analysis
            mock_full.assert_called_once_with(
                self.mock_lsp_client,
                self.cache_path,
                "commit123",
                False,
            )
            self.assertEqual(result, expected_result)

    @patch("static_analyzer.incremental_orchestrator.GitDiffAnalyzer")
    def test_exception_in_fallback_uses_unknown_commit(self, mock_git_analyzer_class):
        """When exception occurs even in fallback, uses 'unknown' commit."""
        # Setup mocks - all calls raise exception
        mock_git_analyzer = Mock()
        mock_git_analyzer.get_current_commit.side_effect = Exception("Git error")
        mock_git_analyzer_class.return_value = mock_git_analyzer

        with patch.object(self.orchestrator, "_perform_full_analysis_and_cache") as mock_full:
            expected_result = create_test_analysis_result()
            mock_full.return_value = expected_result

            result = self.orchestrator.run_incremental_analysis(
                self.mock_lsp_client,
                self.cache_path,
                analyze_cluster_changes=False,
            )

            # Verify fallback with "unknown" commit
            mock_full.assert_called_once_with(
                self.mock_lsp_client,
                self.cache_path,
                "unknown",
                False,
            )
            self.assertEqual(result, expected_result)


class TestPerformFullAnalysisAndCache(unittest.TestCase):
    """Tests for _perform_full_analysis_and_cache method."""

    def setUp(self):
        self.orchestrator = IncrementalAnalysisOrchestrator()
        self.mock_lsp_client = create_mock_lsp_client()
        self.cache_path = Path("/test/cache.json")
        self.commit_hash = "commit123"

    def test_performs_full_analysis_via_lsp(self):
        """Performs full analysis through LSP client."""
        expected_result = create_test_analysis_result()
        self.mock_lsp_client.build_static_analysis.return_value = expected_result

        with patch.object(self.orchestrator.cache_manager, "save_cache_with_clusters"):
            with patch.object(self.orchestrator, "_compute_cluster_results") as mock_cluster:
                mock_cluster.return_value = create_test_cluster_result()

                result = self.orchestrator._perform_full_analysis_and_cache(
                    self.mock_lsp_client,
                    self.cache_path,
                    self.commit_hash,
                    analyze_clusters=True,
                )

                # Verify LSP client was called
                self.mock_lsp_client.build_static_analysis.assert_called_once()
                self.assertEqual(result, expected_result)

    def test_computes_cluster_results_when_enabled(self):
        """Computes cluster results when analyze_clusters=True."""
        expected_result = create_test_analysis_result()
        self.mock_lsp_client.build_static_analysis.return_value = expected_result

        with patch.object(self.orchestrator.cache_manager, "save_cache_with_clusters"):
            with patch.object(self.orchestrator, "_compute_cluster_results") as mock_cluster:
                cluster_results = create_test_cluster_result()
                mock_cluster.return_value = cluster_results

                self.orchestrator._perform_full_analysis_and_cache(
                    self.mock_lsp_client,
                    self.cache_path,
                    self.commit_hash,
                    analyze_clusters=True,
                )

                # Verify cluster computation was called
                mock_cluster.assert_called_once_with(expected_result)

    def test_saves_cache_with_clusters(self):
        """Saves cache with cluster results when computed."""
        expected_result = create_test_analysis_result()
        cluster_results = create_test_cluster_result()
        self.mock_lsp_client.build_static_analysis.return_value = expected_result

        with patch.object(self.orchestrator.cache_manager, "save_cache_with_clusters") as mock_save:
            with patch.object(self.orchestrator, "_compute_cluster_results") as mock_cluster:
                mock_cluster.return_value = cluster_results

                self.orchestrator._perform_full_analysis_and_cache(
                    self.mock_lsp_client,
                    self.cache_path,
                    self.commit_hash,
                    analyze_clusters=True,
                )

                # Verify save was called with clusters
                mock_save.assert_called_once_with(
                    cache_path=self.cache_path,
                    analysis_result=expected_result,
                    cluster_results=cluster_results,
                    commit_hash=self.commit_hash,
                    iteration_id=1,
                )

    def test_saves_cache_without_clusters_when_disabled(self):
        """Saves cache without clusters when analyze_clusters=False."""
        expected_result = create_test_analysis_result()
        self.mock_lsp_client.build_static_analysis.return_value = expected_result

        with patch.object(self.orchestrator.cache_manager, "save_cache") as mock_save:
            self.orchestrator._perform_full_analysis_and_cache(
                self.mock_lsp_client,
                self.cache_path,
                self.commit_hash,
                analyze_clusters=False,
            )

            # Verify save was called without clusters
            mock_save.assert_called_once_with(
                cache_path=self.cache_path,
                analysis_result=expected_result,
                commit_hash=self.commit_hash,
                iteration_id=1,
            )

    def test_continues_when_cache_save_fails(self):
        """Continues and returns result even when cache save fails."""
        expected_result = create_test_analysis_result()
        self.mock_lsp_client.build_static_analysis.return_value = expected_result

        with patch.object(self.orchestrator.cache_manager, "save_cache") as mock_save:
            mock_save.side_effect = Exception("Save failed")

            result = self.orchestrator._perform_full_analysis_and_cache(
                self.mock_lsp_client,
                self.cache_path,
                self.commit_hash,
                analyze_clusters=False,
            )

            # Should still return result despite save failure
            self.assertEqual(result, expected_result)


class TestPerformIncrementalUpdate(unittest.TestCase):
    """Tests for _perform_incremental_update method."""

    def setUp(self):
        self.orchestrator = IncrementalAnalysisOrchestrator()
        self.mock_lsp_client = create_mock_lsp_client()
        self.cache_path = Path("/test/cache.json")
        self.cached_analysis = create_test_analysis_result()
        self.cached_clusters = create_test_cluster_result()
        self.cached_commit = "commit123"
        self.current_commit = "commit456"
        self.cached_iteration = 1

        self.mock_git_analyzer = Mock()

    def test_identifies_changed_files(self):
        """Identifies changed files through git analyzer."""
        changed_files = {Path("/test/file0.py"), Path("/test/file1.py")}
        self.mock_git_analyzer.get_changed_files.return_value = changed_files

        with patch.object(self.orchestrator.cache_manager, "invalidate_files") as mock_invalidate:
            mock_invalidate.return_value = create_test_analysis_result(1)

            with patch.object(self.orchestrator.cache_manager, "merge_results"):
                with patch.object(self.orchestrator.cache_manager, "save_cache_with_clusters"):
                    with patch.object(self.orchestrator, "_compute_cluster_results"):
                        self.orchestrator._perform_incremental_update(
                            self.mock_lsp_client,
                            self.cache_path,
                            self.cached_analysis,
                            self.cached_clusters,
                            self.cached_commit,
                            self.cached_iteration,
                            self.current_commit,
                            self.mock_git_analyzer,
                            analyze_cluster_changes=False,
                        )

                        # Verify git analyzer was called
                        self.mock_git_analyzer.get_changed_files.assert_called_once_with(self.cached_commit)

    def test_handles_no_changed_files(self):
        """Returns cached results when no files changed."""
        self.mock_git_analyzer.get_changed_files.return_value = set()

        result = self.orchestrator._perform_incremental_update(
            self.mock_lsp_client,
            self.cache_path,
            self.cached_analysis,
            self.cached_clusters,
            self.cached_commit,
            self.cached_iteration,
            self.current_commit,
            self.mock_git_analyzer,
            analyze_cluster_changes=False,
        )

        # Should return cached analysis
        self.assertEqual(result, self.cached_analysis)

    def test_invalidates_cache_for_changed_files(self):
        """Invalidates cache for changed files."""
        changed_files = {Path("/test/file0.py")}
        self.mock_git_analyzer.get_changed_files.return_value = changed_files

        with patch.object(self.orchestrator.cache_manager, "invalidate_files") as mock_invalidate:
            mock_invalidate.return_value = create_test_analysis_result(1)

            with patch.object(self.orchestrator.cache_manager, "merge_results"):
                with patch.object(self.orchestrator.cache_manager, "save_cache_with_clusters"):
                    with patch.object(self.orchestrator, "_compute_cluster_results"):
                        self.orchestrator._perform_incremental_update(
                            self.mock_lsp_client,
                            self.cache_path,
                            self.cached_analysis,
                            self.cached_clusters,
                            self.cached_commit,
                            self.cached_iteration,
                            self.current_commit,
                            self.mock_git_analyzer,
                            analyze_cluster_changes=False,
                        )

                        # Verify invalidation was called
                        mock_invalidate.assert_called_once_with(self.cached_analysis, changed_files)

    def test_reanalyzes_existing_changed_files(self):
        """Reanalyzes only existing changed files (not deleted)."""
        # Create temp files to simulate existing files
        with tempfile.TemporaryDirectory() as temp_dir:
            existing_file = Path(temp_dir) / "file0.py"
            existing_file.touch()

            changed_files = {existing_file, Path(temp_dir) / "deleted.py"}  # deleted doesn't exist
            self.mock_git_analyzer.get_changed_files.return_value = changed_files

            with patch.object(self.orchestrator.cache_manager, "invalidate_files") as mock_invalidate:
                mock_invalidate.return_value = create_test_analysis_result(1)

                with patch.object(self.orchestrator.cache_manager, "merge_results"):
                    with patch.object(self.orchestrator.cache_manager, "save_cache_with_clusters"):
                        with patch.object(self.orchestrator, "_compute_cluster_results"):
                            self.orchestrator._perform_incremental_update(
                                self.mock_lsp_client,
                                self.cache_path,
                                self.cached_analysis,
                                self.cached_clusters,
                                self.cached_commit,
                                self.cached_iteration,
                                self.current_commit,
                                self.mock_git_analyzer,
                                analyze_cluster_changes=False,
                            )

                            # Verify only existing file was analyzed
                            self.mock_lsp_client._analyze_specific_files.assert_called_once()
                            analyzed_files = self.mock_lsp_client._analyze_specific_files.call_args[0][0]
                            self.assertEqual(len(analyzed_files), 1)
                            self.assertIn(existing_file, analyzed_files)

    def test_merges_results_correctly(self):
        """Merges new analysis with cached results."""
        changed_files = {Path("/test/file0.py")}
        self.mock_git_analyzer.get_changed_files.return_value = changed_files

        updated_cache = create_test_analysis_result(1)

        with patch.object(self.orchestrator.cache_manager, "invalidate_files") as mock_invalidate:
            mock_invalidate.return_value = updated_cache

            with patch.object(self.orchestrator.cache_manager, "merge_results") as mock_merge:
                merged_result = create_test_analysis_result(2)
                mock_merge.return_value = merged_result

                with patch.object(self.orchestrator.cache_manager, "save_cache_with_clusters"):
                    with patch.object(self.orchestrator, "_compute_cluster_results"):
                        self.orchestrator._perform_incremental_update(
                            self.mock_lsp_client,
                            self.cache_path,
                            self.cached_analysis,
                            self.cached_clusters,
                            self.cached_commit,
                            self.cached_iteration,
                            self.current_commit,
                            self.mock_git_analyzer,
                            analyze_cluster_changes=False,
                        )

                        # Verify merge was called with updated cache and new analysis
                        mock_merge.assert_called_once()
                        call_args = mock_merge.call_args[0]
                        # First arg should be updated cache
                        self.assertEqual(call_args[0], updated_cache)
                        # Second arg should be the new analysis from LSP client
                        self.assertIsInstance(call_args[1], dict)
                        self.assertIn("call_graph", call_args[1])

    def test_saves_updated_cache_with_incremented_iteration(self):
        """Saves updated cache with incremented iteration ID."""
        changed_files = {Path("/test/file0.py")}
        self.mock_git_analyzer.get_changed_files.return_value = changed_files

        with patch.object(self.orchestrator.cache_manager, "invalidate_files"):
            with patch.object(self.orchestrator.cache_manager, "merge_results"):
                with patch.object(self.orchestrator.cache_manager, "save_cache_with_clusters") as mock_save:
                    with patch.object(self.orchestrator, "_compute_cluster_results"):
                        with patch.object(self.orchestrator, "_merge_cluster_results_with_mappings"):
                            self.orchestrator._perform_incremental_update(
                                self.mock_lsp_client,
                                self.cache_path,
                                self.cached_analysis,
                                self.cached_clusters,
                                self.cached_commit,
                                self.cached_iteration,
                                self.current_commit,
                                self.mock_git_analyzer,
                                analyze_cluster_changes=False,
                            )

                            # Verify save was called with incremented iteration
                            mock_save.assert_called_once()
                            call_kwargs = mock_save.call_args[1]
                            self.assertEqual(call_kwargs["iteration_id"], self.cached_iteration + 1)
                            self.assertEqual(call_kwargs["commit_hash"], self.current_commit)

    @patch("static_analyzer.incremental_orchestrator.analyze_cluster_changes_for_languages")
    @patch("static_analyzer.incremental_orchestrator.get_overall_classification")
    def test_analyzes_cluster_changes_when_enabled(self, mock_get_classification, mock_analyze_changes):
        """Analyzes cluster changes when analyze_cluster_changes=True."""
        changed_files = {Path("/test/file0.py")}
        self.mock_git_analyzer.get_changed_files.return_value = changed_files

        # Setup cluster change mocks
        cluster_change_result = ClusterChangeResult(
            classification=ChangeClassification.MEDIUM,
            language="python",
        )
        mock_analyze_changes.return_value = {"python": cluster_change_result}
        mock_get_classification.return_value = ChangeClassification.MEDIUM

        with patch.object(self.orchestrator.cache_manager, "invalidate_files"):
            with patch.object(self.orchestrator.cache_manager, "merge_results"):
                with patch.object(self.orchestrator.cache_manager, "save_cache_with_clusters"):
                    with patch.object(self.orchestrator, "_compute_cluster_results") as mock_compute:
                        new_clusters = create_test_cluster_result()
                        mock_compute.return_value = new_clusters

                        with patch.object(self.orchestrator, "_merge_cluster_results_with_mappings"):
                            result = self.orchestrator._perform_incremental_update(
                                self.mock_lsp_client,
                                self.cache_path,
                                self.cached_analysis,
                                self.cached_clusters,
                                self.cached_commit,
                                self.cached_iteration,
                                self.current_commit,
                                self.mock_git_analyzer,
                                analyze_cluster_changes=True,
                            )

                            # Verify cluster change analysis was performed
                            mock_analyze_changes.assert_called_once()
                            mock_get_classification.assert_called_once()

                            # Verify result structure
                            self.assertIsInstance(result, dict)
                            self.assertIn("analysis_result", result)
                            self.assertIn("cluster_change_result", result)
                            self.assertIn("change_classification", result)
                            self.assertEqual(result["change_classification"], ChangeClassification.MEDIUM)

    def test_returns_dict_format_when_analyze_cluster_changes_true(self):
        """Returns dict with cluster info when analyze_cluster_changes=True."""
        changed_files = {Path("/test/file0.py")}
        self.mock_git_analyzer.get_changed_files.return_value = changed_files

        with patch.object(self.orchestrator.cache_manager, "invalidate_files"):
            with patch.object(self.orchestrator.cache_manager, "merge_results"):
                with patch.object(self.orchestrator.cache_manager, "save_cache_with_clusters"):
                    with patch.object(self.orchestrator, "_compute_cluster_results"):
                        with patch.object(self.orchestrator, "_merge_cluster_results_with_mappings"):
                            result = self.orchestrator._perform_incremental_update(
                                self.mock_lsp_client,
                                self.cache_path,
                                self.cached_analysis,
                                self.cached_clusters,
                                self.cached_commit,
                                self.cached_iteration,
                                self.current_commit,
                                self.mock_git_analyzer,
                                analyze_cluster_changes=True,
                            )

                            # Verify result is a dict with expected keys
                            self.assertIsInstance(result, dict)
                            self.assertIn("analysis_result", result)
                            self.assertIn("cluster_change_result", result)
                            self.assertIn("change_classification", result)


class TestComputeClusterResults(unittest.TestCase):
    """Tests for _compute_cluster_results method."""

    def setUp(self):
        self.orchestrator = IncrementalAnalysisOrchestrator()

    def test_returns_empty_dict_for_empty_call_graph(self):
        """Returns empty dict when call graph has no nodes."""
        analysis_result = {
            "call_graph": CallGraph(),
            "class_hierarchies": {},
            "package_relations": {},
            "references": [],
            "source_files": [],
        }

        result = self.orchestrator._compute_cluster_results(analysis_result)

        self.assertEqual(result, {})

    def test_computes_clusters_for_call_graph_with_nodes(self):
        """Computes clusters when call graph has nodes."""
        call_graph = create_test_call_graph(5)
        analysis_result = {
            "call_graph": call_graph,
            "class_hierarchies": {},
            "package_relations": {},
            "references": [],
            "source_files": [],
        }

        result = self.orchestrator._compute_cluster_results(analysis_result)

        # Should have results for the language
        self.assertIn(call_graph.language, result)
        self.assertIsInstance(result[call_graph.language], ClusterResult)

    def test_uses_call_graph_language_as_key(self):
        """Uses call graph's language as the result key."""
        call_graph = CallGraph(language="typescript")
        for i in range(3):
            node = create_test_node(f"test.func{i}")
            call_graph.add_node(node)

        analysis_result = {
            "call_graph": call_graph,
            "class_hierarchies": {},
            "package_relations": {},
            "references": [],
            "source_files": [],
        }

        result = self.orchestrator._compute_cluster_results(analysis_result)

        self.assertIn("typescript", result)


class TestMatchClustersToOriginal(unittest.TestCase):
    """Tests for _match_clusters_to_original method."""

    def setUp(self):
        self.orchestrator = IncrementalAnalysisOrchestrator()

    def test_returns_empty_when_no_old_clusters_for_language(self):
        """Returns empty mapping when old clusters don't exist for language."""
        new_clusters = create_test_cluster_result()
        old_clusters = {"java": create_test_cluster_result()["python"]}  # Different language

        result = self.orchestrator._match_clusters_to_original(new_clusters, old_clusters)

        # Should have empty mapping for python
        self.assertNotIn("python", result)

    def test_matches_clusters_based_on_file_overlap(self):
        """Matches clusters based on Jaccard similarity of files."""
        # Create old clusters
        old_cluster_result = ClusterResult(
            clusters={
                0: {"func0", "func1"},
                1: {"func2", "func3"},
            },
            file_to_clusters={
                "/test/file0.py": {0},
                "/test/file1.py": {1},
            },
            cluster_to_files={
                0: {"/test/file0.py"},
                1: {"/test/file1.py"},
            },
            strategy="test",
        )
        old_clusters = {"python": old_cluster_result}

        # Create new clusters with mostly same files (should match)
        new_cluster_result = ClusterResult(
            clusters={
                10: {"func0", "func1", "func_new"},  # Mostly same as old cluster 0
                11: {"func2", "func3"},  # Same as old cluster 1
            },
            file_to_clusters={
                "/test/file0.py": {10},
                "/test/file1.py": {11},
            },
            cluster_to_files={
                10: {"/test/file0.py"},
                11: {"/test/file1.py"},
            },
            strategy="test",
        )
        new_clusters = {"python": new_cluster_result}

        result = self.orchestrator._match_clusters_to_original(new_clusters, old_clusters)

        # Should have mappings for python
        self.assertIn("python", result)
        # Should have some matches
        self.assertTrue(len(result["python"]) > 0)

    def test_handles_partial_matching(self):
        """Handles case where only some clusters match."""
        # Create old clusters
        old_cluster_result = ClusterResult(
            clusters={
                0: {"func0", "func1"},
            },
            file_to_clusters={
                "/test/file0.py": {0},
            },
            cluster_to_files={
                0: {"/test/file0.py"},
            },
            strategy="test",
        )
        old_clusters = {"python": old_cluster_result}

        # Create new clusters - one matches, one is new
        new_cluster_result = ClusterResult(
            clusters={
                10: {"func0", "func1"},  # Matches old cluster 0
                11: {"func_new1", "func_new2"},  # Completely new
            },
            file_to_clusters={
                "/test/file0.py": {10},
                "/test/file_new.py": {11},
            },
            cluster_to_files={
                10: {"/test/file0.py"},
                11: {"/test/file_new.py"},
            },
            strategy="test",
        )
        new_clusters = {"python": new_cluster_result}

        result = self.orchestrator._match_clusters_to_original(new_clusters, old_clusters)

        # Should have mapping for python
        self.assertIn("python", result)
        python_mapping = result["python"]

        # Should have exactly one match (cluster 10 -> 0)
        self.assertEqual(len(python_mapping), 1)


class TestMergeClusterResultsWithMappings(unittest.TestCase):
    """Tests for _merge_cluster_results_with_mappings method."""

    def setUp(self):
        self.orchestrator = IncrementalAnalysisOrchestrator()

    def test_returns_new_results_when_no_mappings(self):
        """Returns new results as-is when no mappings provided."""
        new_clusters = create_test_cluster_result()
        old_clusters = create_test_cluster_result()

        result = self.orchestrator._merge_cluster_results_with_mappings(
            new_clusters,
            old_clusters,
            cluster_mappings=None,
        )

        self.assertEqual(result, new_clusters)

    def test_preserves_original_cluster_ids_for_matches(self):
        """Preserves original cluster IDs for matched clusters."""
        # Create old clusters with specific IDs
        old_cluster_result = ClusterResult(
            clusters={
                5: {"func0", "func1"},
            },
            file_to_clusters={
                "/test/file0.py": {5},
            },
            cluster_to_files={
                5: {"/test/file0.py"},
            },
            strategy="test",
        )
        old_clusters = {"python": old_cluster_result}

        # Create new clusters with different IDs
        new_cluster_result = ClusterResult(
            clusters={
                0: {"func0", "func1"},  # Should be remapped to ID 5
            },
            file_to_clusters={
                "/test/file0.py": {0},
            },
            cluster_to_files={
                0: {"/test/file0.py"},
            },
            strategy="test",
        )
        new_clusters = {"python": new_cluster_result}

        # Mapping: new ID 0 -> old ID 5
        cluster_mappings = {"python": {0: 5}}

        result = self.orchestrator._merge_cluster_results_with_mappings(
            new_clusters,
            old_clusters,
            cluster_mappings,
        )

        # Result should have cluster ID 5 (original ID)
        self.assertIn("python", result)
        python_result = result["python"]
        self.assertIn(5, python_result.get_cluster_ids())
        self.assertNotIn(0, python_result.get_cluster_ids())

    def test_assigns_new_ids_to_unmapped_clusters(self):
        """Assigns new IDs to clusters that don't have mappings."""
        # Create old clusters
        old_cluster_result = ClusterResult(
            clusters={
                5: {"func0", "func1"},
            },
            file_to_clusters={
                "/test/file0.py": {5},
            },
            cluster_to_files={
                5: {"/test/file0.py"},
            },
            strategy="test",
        )
        old_clusters = {"python": old_cluster_result}

        # Create new clusters - one mapped, one unmapped
        new_cluster_result = ClusterResult(
            clusters={
                0: {"func0", "func1"},  # Maps to 5
                1: {"func_new"},  # No mapping - should get new ID
            },
            file_to_clusters={
                "/test/file0.py": {0},
                "/test/file_new.py": {1},
            },
            cluster_to_files={
                0: {"/test/file0.py"},
                1: {"/test/file_new.py"},
            },
            strategy="test",
        )
        new_clusters = {"python": new_cluster_result}

        # Only map cluster 0
        cluster_mappings = {"python": {0: 5}}

        result = self.orchestrator._merge_cluster_results_with_mappings(
            new_clusters,
            old_clusters,
            cluster_mappings,
        )

        python_result = result["python"]
        cluster_ids = python_result.get_cluster_ids()

        # Should have 2 clusters: the mapped one (5) and a new one
        self.assertEqual(len(cluster_ids), 2)
        self.assertIn(5, cluster_ids)

        # The new cluster should have ID > 5
        new_id = [cid for cid in cluster_ids if cid != 5][0]
        self.assertGreater(new_id, 5)

    def test_handles_multiple_languages(self):
        """Handles merging for multiple languages."""
        # Create clusters for multiple languages
        old_clusters = {
            "python": ClusterResult(
                clusters={0: {"func0"}},
                file_to_clusters={"/test/file.py": {0}},
                cluster_to_files={0: {"/test/file.py"}},
                strategy="test",
            ),
            "typescript": ClusterResult(
                clusters={0: {"func1"}},
                file_to_clusters={"/test/file.ts": {0}},
                cluster_to_files={0: {"/test/file.ts"}},
                strategy="test",
            ),
        }

        new_clusters = {
            "python": ClusterResult(
                clusters={10: {"func0"}},
                file_to_clusters={"/test/file.py": {10}},
                cluster_to_files={10: {"/test/file.py"}},
                strategy="test",
            ),
            "typescript": ClusterResult(
                clusters={20: {"func1"}},
                file_to_clusters={"/test/file.ts": {20}},
                cluster_to_files={20: {"/test/file.ts"}},
                strategy="test",
            ),
        }

        cluster_mappings = {
            "python": {10: 0},
            "typescript": {20: 0},
        }

        result = self.orchestrator._merge_cluster_results_with_mappings(
            new_clusters,
            old_clusters,
            cluster_mappings,
        )

        # Should have both languages
        self.assertIn("python", result)
        self.assertIn("typescript", result)


if __name__ == "__main__":
    unittest.main()
