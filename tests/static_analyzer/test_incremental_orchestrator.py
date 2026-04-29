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


def _make_ignore_manager() -> Mock:
    """Create a mock RepoIgnoreManager that allows all files."""
    mgr = Mock()
    mgr.should_ignore.return_value = False
    return mgr


def create_mock_engine_args() -> tuple[Mock, Path, Mock, Path]:
    """Create mock (adapter, project_path, engine_client, cache_path) for engine-based orchestrator."""
    mock_adapter = Mock()
    mock_adapter.language = "Python"
    mock_adapter.file_extensions = {".py"}
    project_path = Path("/test/project")
    mock_engine_client = Mock()
    mock_engine_client.get_collected_diagnostics.return_value = {}
    cache_path = Path("/test/cache/incremental_cache_python.json")
    return mock_adapter, project_path, mock_engine_client, cache_path


class TestIncrementalAnalysisOrchestratorInit(unittest.TestCase):
    """Tests for IncrementalAnalysisOrchestrator initialization."""

    def test_init_creates_cache_manager(self):
        """Test that __init__ creates an AnalysisCacheManager with the repo root."""
        ignore_manager = _make_ignore_manager()
        orchestrator = IncrementalAnalysisOrchestrator(ignore_manager)
        self.assertIsNotNone(orchestrator.cache_manager)
        self.assertEqual(orchestrator.cache_manager.repo_root, ignore_manager.repo_root)

    def test_init_creates_cluster_analyzer(self):
        """Test that __init__ creates a ClusterChangeAnalyzer."""
        orchestrator = IncrementalAnalysisOrchestrator(_make_ignore_manager())
        self.assertIsNotNone(orchestrator.cluster_analyzer)
        self.assertIsInstance(orchestrator.cluster_analyzer, ClusterChangeAnalyzer)


class TestRunIncrementalAnalysisNoCache(unittest.TestCase):
    """Tests for run_incremental_analysis when no cache exists."""

    def setUp(self):
        self.mock_adapter, self.project_path, self.mock_engine_client, self.cache_path = create_mock_engine_args()
        self.orchestrator = IncrementalAnalysisOrchestrator(_make_ignore_manager())

    @patch("static_analyzer.incremental_orchestrator.require_current_commit", return_value="commit123")
    def test_no_cache_performs_full_analysis(self, _mock_commit):
        """Test that no cache triggers full analysis."""
        with patch.object(self.orchestrator.cache_manager, "load_cache_with_clusters") as mock_load:
            mock_load.return_value = None  # No cache

            with patch.object(self.orchestrator, "_perform_full_analysis_and_cache") as mock_full:
                expected_result = create_test_analysis_result()
                mock_full.return_value = expected_result

                result = self.orchestrator.run_incremental_analysis(
                    self.mock_adapter,
                    self.project_path,
                    self.mock_engine_client,
                    self.cache_path,
                    analyze_cluster_changes=False,
                )

                mock_full.assert_called_once_with(
                    self.mock_adapter,
                    self.project_path,
                    self.mock_engine_client,
                    self.cache_path,
                    "commit123",
                )

    @patch("static_analyzer.incremental_orchestrator.require_current_commit", return_value="commit123")
    def test_no_cache_returns_big_classification(self, _mock_commit):
        """Test that no cache returns BIG classification when cluster changes enabled."""
        with patch.object(self.orchestrator.cache_manager, "load_cache_with_clusters") as mock_load:
            mock_load.return_value = None

            with patch.object(self.orchestrator, "_perform_full_analysis_and_cache") as mock_full:
                mock_full.return_value = create_test_analysis_result()

                result = self.orchestrator.run_incremental_analysis(
                    self.mock_adapter,
                    self.project_path,
                    self.mock_engine_client,
                    self.cache_path,
                    analyze_cluster_changes=True,
                )

                self.assertIsInstance(result, dict)
                self.assertEqual(result["change_classification"], ChangeClassification.BIG)


class TestRunIncrementalAnalysisCachedNoChanges(unittest.TestCase):
    """Tests for run_incremental_analysis when cache exists and no changes."""

    def setUp(self):
        self.mock_adapter, self.project_path, self.mock_engine_client, self.cache_path = create_mock_engine_args()
        self.orchestrator = IncrementalAnalysisOrchestrator(_make_ignore_manager())

    @patch("static_analyzer.incremental_orchestrator.has_uncommitted_changes", return_value=False)
    @patch("static_analyzer.incremental_orchestrator.require_current_commit", return_value="cached_commit")
    def test_cached_no_changes_returns_cached(self, _mock_commit, _mock_dirty):
        """Test that matching commit returns cached results."""
        cached_analysis = create_test_analysis_result()
        cached_clusters = create_test_cluster_result()

        with patch.object(self.orchestrator.cache_manager, "load_cache_with_clusters") as mock_load:
            mock_load.return_value = (cached_analysis, cached_clusters, "cached_commit", 1)

            result = self.orchestrator.run_incremental_analysis(
                self.mock_adapter,
                self.project_path,
                self.mock_engine_client,
                self.cache_path,
                analyze_cluster_changes=False,
            )

            self.assertEqual(result, cached_analysis)

    @patch("static_analyzer.incremental_orchestrator.has_uncommitted_changes", return_value=False)
    @patch("static_analyzer.incremental_orchestrator.require_current_commit", return_value="cached_commit")
    def test_cached_no_changes_returns_small_classification(self, _mock_commit, _mock_dirty):
        """Test that matching commit returns SMALL classification."""
        cached_analysis = create_test_analysis_result()
        cached_clusters = create_test_cluster_result()

        with patch.object(self.orchestrator.cache_manager, "load_cache_with_clusters") as mock_load:
            mock_load.return_value = (cached_analysis, cached_clusters, "cached_commit", 1)

            result = self.orchestrator.run_incremental_analysis(
                self.mock_adapter,
                self.project_path,
                self.mock_engine_client,
                self.cache_path,
                analyze_cluster_changes=True,
            )

            self.assertIsInstance(result, dict)
            self.assertEqual(result["change_classification"], ChangeClassification.SMALL)


class TestRunIncrementalAnalysisWithChanges(unittest.TestCase):
    """Tests for run_incremental_analysis when changes are detected."""

    def setUp(self):
        self.mock_adapter, self.project_path, self.mock_engine_client, self.cache_path = create_mock_engine_args()
        self.orchestrator = IncrementalAnalysisOrchestrator(_make_ignore_manager())

    @patch("static_analyzer.incremental_orchestrator.has_uncommitted_changes", return_value=True)
    @patch("static_analyzer.incremental_orchestrator.require_current_commit", return_value="cached_commit")
    def test_uncommitted_changes_triggers_incremental(self, _mock_commit, _mock_dirty):
        """Test that uncommitted changes trigger incremental update."""
        cached_analysis = create_test_analysis_result()
        cached_clusters = create_test_cluster_result()

        with patch.object(self.orchestrator.cache_manager, "load_cache_with_clusters") as mock_load:
            mock_load.return_value = (cached_analysis, cached_clusters, "cached_commit", 1)

            with patch.object(self.orchestrator, "_perform_incremental_update") as mock_incremental:
                mock_incremental.return_value = create_test_analysis_result()

                self.orchestrator.run_incremental_analysis(
                    self.mock_adapter,
                    self.project_path,
                    self.mock_engine_client,
                    self.cache_path,
                )

                mock_incremental.assert_called_once()

    @patch("static_analyzer.incremental_orchestrator.has_uncommitted_changes", return_value=False)
    @patch("static_analyzer.incremental_orchestrator.require_current_commit", return_value="new_commit")
    def test_committed_changes_triggers_incremental(self, _mock_commit, _mock_dirty):
        """Test that new commit triggers incremental update."""
        cached_analysis = create_test_analysis_result()
        cached_clusters = create_test_cluster_result()

        with patch.object(self.orchestrator.cache_manager, "load_cache_with_clusters") as mock_load:
            mock_load.return_value = (cached_analysis, cached_clusters, "old_commit", 1)

            with patch.object(self.orchestrator, "_perform_incremental_update") as mock_incremental:
                mock_incremental.return_value = create_test_analysis_result()

                self.orchestrator.run_incremental_analysis(
                    self.mock_adapter,
                    self.project_path,
                    self.mock_engine_client,
                    self.cache_path,
                )

                mock_incremental.assert_called_once()


class TestRunIncrementalAnalysisExceptionHandling(unittest.TestCase):
    """Tests for exception handling in run_incremental_analysis."""

    def setUp(self):
        self.mock_adapter, self.project_path, self.mock_engine_client, self.cache_path = create_mock_engine_args()
        self.orchestrator = IncrementalAnalysisOrchestrator(_make_ignore_manager())

    @patch("static_analyzer.incremental_orchestrator.require_current_commit", return_value="commit123")
    def test_exception_falls_back_to_full_analysis(self, _mock_commit):
        """Test that exceptions trigger fallback to full analysis."""
        with patch.object(self.orchestrator.cache_manager, "load_cache_with_clusters") as mock_load:
            mock_load.side_effect = Exception("Cache error")

            with patch.object(self.orchestrator, "_perform_full_analysis_and_cache") as mock_full:
                mock_full.return_value = create_test_analysis_result()

                self.orchestrator.run_incremental_analysis(
                    self.mock_adapter,
                    self.project_path,
                    self.mock_engine_client,
                    self.cache_path,
                )

                mock_full.assert_called_once_with(
                    self.mock_adapter,
                    self.project_path,
                    self.mock_engine_client,
                    self.cache_path,
                    "commit123",
                    True,
                )

    @patch("static_analyzer.incremental_orchestrator.require_current_commit", side_effect=Exception("Git error"))
    def test_exception_in_fallback_uses_unknown_commit(self, _mock_commit):
        """Test that if require_current_commit fails in fallback, 'unknown' is used."""
        with patch.object(self.orchestrator, "_perform_full_analysis_and_cache") as mock_full:
            mock_full.return_value = create_test_analysis_result()

            self.orchestrator.run_incremental_analysis(
                self.mock_adapter,
                self.project_path,
                self.mock_engine_client,
                self.cache_path,
            )

            mock_full.assert_called_once_with(
                self.mock_adapter,
                self.project_path,
                self.mock_engine_client,
                self.cache_path,
                "unknown",
                True,
            )


class TestPerformFullAnalysisAndCache(unittest.TestCase):
    """Tests for _perform_full_analysis_and_cache method."""

    def setUp(self):
        self.mock_adapter, self.project_path, self.mock_engine_client, self.cache_path = create_mock_engine_args()
        self.mock_adapter.discover_source_files.return_value = [Path("/test/file0.py")]
        self.orchestrator = IncrementalAnalysisOrchestrator(_make_ignore_manager())

    @patch("static_analyzer.incremental_orchestrator.convert_to_codeboarding_format")
    @patch("static_analyzer.incremental_orchestrator.CallGraphBuilder")
    def test_performs_full_analysis_via_engine(self, mock_builder_class, mock_convert):
        """Test that full analysis uses the engine pipeline."""
        expected_result = create_test_analysis_result()

        mock_builder = Mock()
        mock_engine_result = Mock()
        mock_builder.build.return_value = mock_engine_result
        mock_builder_class.return_value = mock_builder

        mock_convert.return_value = expected_result

        with patch.object(self.orchestrator.cache_manager, "save_cache"):
            result = self.orchestrator._perform_full_analysis_and_cache(
                self.mock_adapter,
                self.project_path,
                self.mock_engine_client,
                self.cache_path,
                "commit123",
                False,
            )

            self.mock_adapter.discover_source_files.assert_called_once()
            mock_builder_class.assert_called_once_with(self.mock_engine_client, self.mock_adapter, self.project_path)
            mock_builder.build.assert_called_once()
            mock_convert.assert_called_once()
            self.assertEqual(result, expected_result)

    @patch("static_analyzer.incremental_orchestrator.convert_to_codeboarding_format")
    @patch("static_analyzer.incremental_orchestrator.CallGraphBuilder")
    def test_computes_cluster_results_when_enabled(self, mock_builder_class, mock_convert):
        """Test that cluster results are computed when analyze_clusters=True."""
        expected_result = create_test_analysis_result()

        mock_builder = Mock()
        mock_builder.build.return_value = Mock()
        mock_builder_class.return_value = mock_builder
        mock_convert.return_value = expected_result

        with patch.object(self.orchestrator.cache_manager, "save_cache_with_clusters"):
            with patch.object(self.orchestrator, "_compute_cluster_results") as mock_cluster:
                mock_cluster.return_value = create_test_cluster_result()

                self.orchestrator._perform_full_analysis_and_cache(
                    self.mock_adapter,
                    self.project_path,
                    self.mock_engine_client,
                    self.cache_path,
                    "commit123",
                    True,
                )

                mock_cluster.assert_called_once()

    @patch("static_analyzer.incremental_orchestrator.convert_to_codeboarding_format")
    @patch("static_analyzer.incremental_orchestrator.CallGraphBuilder")
    def test_saves_cache_with_clusters(self, mock_builder_class, mock_convert):
        """Test that cache is saved with cluster results."""
        expected_result = create_test_analysis_result()

        mock_builder = Mock()
        mock_builder.build.return_value = Mock()
        mock_builder_class.return_value = mock_builder
        mock_convert.return_value = expected_result

        with patch.object(self.orchestrator.cache_manager, "save_cache_with_clusters") as mock_save:
            with patch.object(self.orchestrator, "_compute_cluster_results") as mock_cluster:
                mock_cluster.return_value = create_test_cluster_result()

                self.orchestrator._perform_full_analysis_and_cache(
                    self.mock_adapter,
                    self.project_path,
                    self.mock_engine_client,
                    self.cache_path,
                    "commit123",
                    True,
                )

                mock_save.assert_called_once()

    @patch("static_analyzer.incremental_orchestrator.convert_to_codeboarding_format")
    @patch("static_analyzer.incremental_orchestrator.CallGraphBuilder")
    def test_saves_cache_without_clusters_when_disabled(self, mock_builder_class, mock_convert):
        """Test that cache is saved without clusters when disabled."""
        expected_result = create_test_analysis_result()

        mock_builder = Mock()
        mock_builder.build.return_value = Mock()
        mock_builder_class.return_value = mock_builder
        mock_convert.return_value = expected_result

        with patch.object(self.orchestrator.cache_manager, "save_cache") as mock_save:
            self.orchestrator._perform_full_analysis_and_cache(
                self.mock_adapter,
                self.project_path,
                self.mock_engine_client,
                self.cache_path,
                "commit123",
                False,
            )

            mock_save.assert_called_once()

    @patch("static_analyzer.incremental_orchestrator.convert_to_codeboarding_format")
    @patch("static_analyzer.incremental_orchestrator.CallGraphBuilder")
    def test_continues_when_cache_save_fails(self, mock_builder_class, mock_convert):
        """Test that analysis result is returned even when cache save fails."""
        expected_result = create_test_analysis_result()

        mock_builder = Mock()
        mock_builder.build.return_value = Mock()
        mock_builder_class.return_value = mock_builder
        mock_convert.return_value = expected_result

        with patch.object(self.orchestrator.cache_manager, "save_cache") as mock_save:
            mock_save.side_effect = Exception("Save failed")

            result = self.orchestrator._perform_full_analysis_and_cache(
                self.mock_adapter,
                self.project_path,
                self.mock_engine_client,
                self.cache_path,
                "commit123",
                False,
            )

            self.assertEqual(result, expected_result)


class TestPerformIncrementalUpdate(unittest.TestCase):
    """Tests for _perform_incremental_update method."""

    def setUp(self):
        self.mock_adapter, self.project_path, self.mock_engine_client, self.cache_path = create_mock_engine_args()
        self.orchestrator = IncrementalAnalysisOrchestrator(_make_ignore_manager())
        self.cached_analysis = create_test_analysis_result()
        self.cached_cluster_results = create_test_cluster_result()

    def _patch_changed_files(self, changed: set) -> None:
        patcher = patch(
            "static_analyzer.incremental_orchestrator.get_changed_files_since",
            return_value=changed,
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def _invoke(self, analyze_cluster_changes: bool = True):
        return self.orchestrator._perform_incremental_update(
            self.mock_adapter,
            self.project_path,
            self.mock_engine_client,
            self.cache_path,
            self.cached_analysis,
            self.cached_cluster_results,
            "old_commit",
            1,
            "new_commit",
            analyze_cluster_changes,
        )

    def test_handles_no_changed_files(self):
        """Test that no changed files returns cached results."""
        self._patch_changed_files(set())

        result = self._invoke(analyze_cluster_changes=False)

        self.assertEqual(result, self.cached_analysis)

    def test_no_changed_files_returns_small_classification(self):
        """Test that no changed files returns SMALL classification."""
        self._patch_changed_files(set())

        result = self._invoke(analyze_cluster_changes=True)

        self.assertIsInstance(result, dict)
        self.assertEqual(result["change_classification"], ChangeClassification.SMALL)

    @patch("static_analyzer.incremental_orchestrator.convert_to_codeboarding_format")
    @patch("static_analyzer.incremental_orchestrator.CallGraphBuilder")
    def test_reanalyzes_existing_changed_files(self, mock_builder_class, mock_convert):
        """Test that existing changed files are reanalyzed."""
        changed_file = MagicMock(spec=Path)
        changed_file.exists.return_value = True
        changed_file.suffix = ".py"
        changed_file.__str__ = lambda x: "/test/changed.py"  # type: ignore[misc]

        self._patch_changed_files({changed_file})

        mock_builder = Mock()
        mock_builder.build.return_value = Mock()
        mock_builder_class.return_value = mock_builder
        mock_convert.return_value = create_test_analysis_result(1)

        with patch.object(self.orchestrator.cache_manager, "invalidate_files") as mock_invalidate:
            mock_invalidate.return_value = self.cached_analysis
            with patch.object(self.orchestrator.cache_manager, "merge_results") as mock_merge:
                mock_merge.return_value = create_test_analysis_result()
                with patch.object(self.orchestrator, "_compute_cluster_results") as mock_cluster:
                    mock_cluster.return_value = create_test_cluster_result()
                    with patch.object(self.orchestrator.cache_manager, "save_cache_with_clusters"):
                        self._invoke()

                        mock_builder_class.assert_called_once_with(
                            self.mock_engine_client, self.mock_adapter, self.project_path
                        )
                        mock_builder.build.assert_called_once()

    @patch("static_analyzer.incremental_orchestrator.convert_to_codeboarding_format")
    @patch("static_analyzer.incremental_orchestrator.CallGraphBuilder")
    def test_merges_results_correctly(self, mock_builder_class, mock_convert):
        """Test that new and cached results are merged."""
        changed_file = MagicMock(spec=Path)
        changed_file.exists.return_value = True
        changed_file.suffix = ".py"
        changed_file.__str__ = lambda x: "/test/changed.py"  # type: ignore[misc]

        self._patch_changed_files({changed_file})

        mock_builder = Mock()
        mock_builder.build.return_value = Mock()
        mock_builder_class.return_value = mock_builder
        mock_convert.return_value = create_test_analysis_result(1)

        with patch.object(self.orchestrator.cache_manager, "invalidate_files") as mock_invalidate:
            mock_invalidate.return_value = self.cached_analysis
            with patch.object(self.orchestrator.cache_manager, "merge_results") as mock_merge:
                mock_merge.return_value = create_test_analysis_result()
                with patch.object(self.orchestrator, "_compute_cluster_results") as mock_cluster:
                    mock_cluster.return_value = create_test_cluster_result()
                    with patch.object(self.orchestrator.cache_manager, "save_cache_with_clusters"):
                        self._invoke()

                        mock_merge.assert_called_once()

    @patch("static_analyzer.incremental_orchestrator.get_overall_classification")
    @patch("static_analyzer.incremental_orchestrator.analyze_cluster_changes_for_languages")
    @patch("static_analyzer.incremental_orchestrator.convert_to_codeboarding_format")
    @patch("static_analyzer.incremental_orchestrator.CallGraphBuilder")
    def test_analyzes_cluster_changes_when_enabled(
        self, mock_builder_class, mock_convert, mock_analyze_changes, mock_get_classification
    ):
        """Test cluster change analysis when enabled."""
        changed_file = MagicMock(spec=Path)
        changed_file.exists.return_value = True
        changed_file.suffix = ".py"
        changed_file.__str__ = lambda x: "/test/changed.py"  # type: ignore[misc]

        self._patch_changed_files({changed_file})

        mock_builder = Mock()
        mock_builder.build.return_value = Mock()
        mock_builder_class.return_value = mock_builder
        mock_convert.return_value = create_test_analysis_result(1)

        mock_analyze_changes.return_value = {"python": Mock(spec=ClusterChangeResult)}
        mock_get_classification.return_value = ChangeClassification.MEDIUM

        with patch.object(self.orchestrator.cache_manager, "invalidate_files") as mock_invalidate:
            mock_invalidate.return_value = self.cached_analysis
            with patch.object(self.orchestrator.cache_manager, "merge_results") as mock_merge:
                mock_merge.return_value = create_test_analysis_result()
                with patch.object(self.orchestrator, "_compute_cluster_results") as mock_cluster:
                    mock_cluster.return_value = create_test_cluster_result()
                    with patch.object(self.orchestrator.cache_manager, "save_cache_with_clusters"):
                        result = self._invoke(analyze_cluster_changes=True)

                        mock_analyze_changes.assert_called_once()
                        self.assertEqual(result["change_classification"], ChangeClassification.MEDIUM)

    @patch("static_analyzer.incremental_orchestrator.convert_to_codeboarding_format")
    @patch("static_analyzer.incremental_orchestrator.CallGraphBuilder")
    def test_returns_dict_format_when_analyze_cluster_changes_true(self, mock_builder_class, mock_convert):
        """Test that result is dict with required keys when cluster changes enabled."""
        changed_file = MagicMock(spec=Path)
        changed_file.exists.return_value = True
        changed_file.suffix = ".py"
        changed_file.__str__ = lambda x: "/test/changed.py"  # type: ignore[misc]

        self._patch_changed_files({changed_file})

        mock_builder = Mock()
        mock_builder.build.return_value = Mock()
        mock_builder_class.return_value = mock_builder
        mock_convert.return_value = create_test_analysis_result(1)

        with patch.object(self.orchestrator.cache_manager, "invalidate_files") as mock_invalidate:
            mock_invalidate.return_value = self.cached_analysis
            with patch.object(self.orchestrator.cache_manager, "merge_results") as mock_merge:
                mock_merge.return_value = create_test_analysis_result()
                with patch.object(self.orchestrator, "_compute_cluster_results") as mock_cluster:
                    mock_cluster.return_value = create_test_cluster_result()
                    with patch.object(self.orchestrator.cache_manager, "save_cache_with_clusters"):
                        result = self._invoke(analyze_cluster_changes=True)

                        self.assertIn("analysis_result", result)
                        self.assertIn("cluster_change_result", result)
                        self.assertIn("change_classification", result)


class TestComputeClusterResults(unittest.TestCase):
    """Tests for _compute_cluster_results method."""

    def setUp(self):
        self.orchestrator = IncrementalAnalysisOrchestrator(_make_ignore_manager())

    def test_returns_empty_dict_for_empty_call_graph(self):
        """Test that an empty call graph returns empty cluster results."""
        analysis_result = {
            "call_graph": CallGraph(),
            "references": [],
            "class_hierarchies": {},
            "package_relations": {},
            "source_files": [],
        }

        result = self.orchestrator._compute_cluster_results(analysis_result)

        self.assertEqual(result, {})

    def test_computes_clusters_for_call_graph_with_nodes(self):
        """Test that clusters are computed when call graph has nodes."""
        analysis_result = create_test_analysis_result(5)

        result = self.orchestrator._compute_cluster_results(analysis_result)

        self.assertIsInstance(result, dict)
        # Should have at least one language
        self.assertGreater(len(result), 0)

    def test_uses_call_graph_language_as_key(self):
        """Test that the call graph's language is used as the key."""
        call_graph = create_test_call_graph(3)
        call_graph.language = "test_lang"
        analysis_result = {
            "call_graph": call_graph,
            "references": [],
            "class_hierarchies": {},
            "package_relations": {},
            "source_files": [],
        }

        result = self.orchestrator._compute_cluster_results(analysis_result)

        self.assertIn("test_lang", result)


class TestMatchClustersToOriginal(unittest.TestCase):
    """Tests for _match_clusters_to_original method."""

    def setUp(self):
        self.orchestrator = IncrementalAnalysisOrchestrator(_make_ignore_manager())

    def test_returns_empty_when_no_old_clusters_for_language(self):
        """Test that no matching is done when old clusters don't have the language."""
        new_results = create_test_cluster_result()
        old_results: dict[str, ClusterResult] = {}

        result = self.orchestrator._match_clusters_to_original(new_results, old_results)

        self.assertEqual(result, {})

    def test_matches_clusters_based_on_file_overlap(self):
        """Test that clusters are matched based on shared files."""
        # Create old clusters with known files
        old_clusters = {
            0: {"func_a", "func_b"},
            1: {"func_c", "func_d"},
        }
        old_file_to_clusters: dict[str, set[int]] = {
            "/test/a.py": {0},
            "/test/b.py": {1},
        }
        old_cluster_to_files = {
            0: {"/test/a.py"},
            1: {"/test/b.py"},
        }
        old_result = ClusterResult(
            clusters=old_clusters,
            file_to_clusters=old_file_to_clusters,
            cluster_to_files=old_cluster_to_files,
            strategy="test",
        )

        # Create new clusters that overlap with old ones
        new_clusters = {
            0: {"func_a", "func_b", "func_e"},
            1: {"func_c", "func_d"},
        }
        new_file_to_clusters: dict[str, set[int]] = {
            "/test/a.py": {0},
            "/test/b.py": {1},
        }
        new_cluster_to_files = {
            0: {"/test/a.py"},
            1: {"/test/b.py"},
        }
        new_result = ClusterResult(
            clusters=new_clusters,
            file_to_clusters=new_file_to_clusters,
            cluster_to_files=new_cluster_to_files,
            strategy="test",
        )

        result = self.orchestrator._match_clusters_to_original(
            {"python": new_result},
            {"python": old_result},
        )

        self.assertIn("python", result)
        # Both clusters should match: new 0 -> old 0, new 1 -> old 1
        self.assertEqual(result["python"].get(0), 0)
        self.assertEqual(result["python"].get(1), 1)

    def test_handles_partial_matching(self):
        """Test that only clusters with sufficient overlap are matched."""
        old_clusters = {0: {"func_a"}}
        old_file_to_clusters: dict[str, set[int]] = {"/test/a.py": {0}}
        old_cluster_to_files = {0: {"/test/a.py"}}
        old_result = ClusterResult(
            clusters=old_clusters,
            file_to_clusters=old_file_to_clusters,
            cluster_to_files=old_cluster_to_files,
            strategy="test",
        )

        # New cluster has completely different files
        new_clusters = {0: {"func_x"}}
        new_file_to_clusters: dict[str, set[int]] = {"/test/x.py": {0}}
        new_cluster_to_files = {0: {"/test/x.py"}}
        new_result = ClusterResult(
            clusters=new_clusters,
            file_to_clusters=new_file_to_clusters,
            cluster_to_files=new_cluster_to_files,
            strategy="test",
        )

        result = self.orchestrator._match_clusters_to_original(
            {"python": new_result},
            {"python": old_result},
        )

        # No clusters should match (no file overlap)
        self.assertEqual(result.get("python", {}), {})


class TestMergeClusterResultsWithMappings(unittest.TestCase):
    """Tests for _merge_cluster_results_with_mappings method."""

    def setUp(self):
        self.orchestrator = IncrementalAnalysisOrchestrator(_make_ignore_manager())

    def test_returns_new_results_when_no_mappings(self):
        """Test that new results are returned unchanged when no mappings exist."""
        new_results = create_test_cluster_result()

        result = self.orchestrator._merge_cluster_results_with_mappings(new_results, {}, None)

        self.assertEqual(result, new_results)

    def test_preserves_original_cluster_ids_for_matches(self):
        """Test that matched clusters use original IDs."""
        new_clusters = {
            0: {"func_a", "func_b"},
            1: {"func_c", "func_d"},
        }
        new_file_to_clusters: dict[str, set[int]] = {
            "/test/a.py": {0},
            "/test/b.py": {1},
        }
        new_cluster_to_files = {
            0: {"/test/a.py"},
            1: {"/test/b.py"},
        }
        new_result = ClusterResult(
            clusters=new_clusters,
            file_to_clusters=new_file_to_clusters,
            cluster_to_files=new_cluster_to_files,
            strategy="test",
        )

        old_clusters = {
            10: {"func_a", "func_b"},
            20: {"func_c", "func_d"},
        }
        old_file_to_clusters: dict[str, set[int]] = {
            "/test/a.py": {10},
            "/test/b.py": {20},
        }
        old_cluster_to_files = {
            10: {"/test/a.py"},
            20: {"/test/b.py"},
        }
        old_result = ClusterResult(
            clusters=old_clusters,
            file_to_clusters=old_file_to_clusters,
            cluster_to_files=old_cluster_to_files,
            strategy="test",
        )

        # Map new cluster 0 -> old cluster 10, new cluster 1 -> old cluster 20
        mappings = {"python": {0: 10, 1: 20}}

        result = self.orchestrator._merge_cluster_results_with_mappings(
            {"python": new_result},
            {"python": old_result},
            mappings,
        )

        self.assertIn("python", result)
        merged = result["python"]
        cluster_ids = merged.get_cluster_ids()
        self.assertIn(10, cluster_ids)
        self.assertIn(20, cluster_ids)

    def test_assigns_new_ids_to_unmapped_clusters(self):
        """Test that unmapped clusters get new IDs."""
        new_clusters = {
            0: {"func_a"},
            1: {"func_new"},
        }
        new_file_to_clusters: dict[str, set[int]] = {
            "/test/a.py": {0},
            "/test/new.py": {1},
        }
        new_cluster_to_files = {
            0: {"/test/a.py"},
            1: {"/test/new.py"},
        }
        new_result = ClusterResult(
            clusters=new_clusters,
            file_to_clusters=new_file_to_clusters,
            cluster_to_files=new_cluster_to_files,
            strategy="test",
        )

        old_clusters = {10: {"func_a"}}
        old_file_to_clusters: dict[str, set[int]] = {"/test/a.py": {10}}
        old_cluster_to_files = {10: {"/test/a.py"}}
        old_result = ClusterResult(
            clusters=old_clusters,
            file_to_clusters=old_file_to_clusters,
            cluster_to_files=old_cluster_to_files,
            strategy="test",
        )

        # Only map new cluster 0 -> old cluster 10
        mappings = {"python": {0: 10}}

        result = self.orchestrator._merge_cluster_results_with_mappings(
            {"python": new_result},
            {"python": old_result},
            mappings,
        )

        merged = result["python"]
        cluster_ids = merged.get_cluster_ids()
        self.assertIn(10, cluster_ids)
        # The unmapped cluster should get ID 11 (max old ID + 1)
        self.assertEqual(len(cluster_ids), 2)


if __name__ == "__main__":
    unittest.main()
