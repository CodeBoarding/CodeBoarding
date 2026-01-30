"""Tests for the IncrementalAnalysisOrchestrator class."""

import tempfile
import shutil
import subprocess
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from static_analyzer.incremental_orchestrator import IncrementalAnalysisOrchestrator
from static_analyzer.graph import CallGraph, Node


class TestIncrementalAnalysisOrchestrator(unittest.TestCase):
    """Tests for IncrementalAnalysisOrchestrator functionality."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.cache_path = Path(self.temp_dir) / "test_cache.json"
        self.orchestrator = IncrementalAnalysisOrchestrator()

        # Create a mock LSP client
        self.mock_lsp_client = Mock()
        self.mock_lsp_client.project_path = Path(self.temp_dir)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _create_sample_analysis_result(self) -> dict:
        """Create a sample analysis result for testing."""
        call_graph = CallGraph()
        node1 = Node("module.function1", 6, "/path/to/file1.py", 10, 20)
        call_graph.add_node(node1)

        return {
            "call_graph": call_graph,
            "class_hierarchies": {},
            "package_relations": {},
            "references": [node1],
            "source_files": [Path("/path/to/file1.py")],
        }

    def test_should_use_cache_returns_false_for_missing_cache(self):
        """_should_use_cache should return False when cache doesn't exist."""
        result = self.orchestrator._should_use_cache(Path("/nonexistent/cache.json"))
        self.assertFalse(result)

    def test_should_use_cache_returns_false_for_invalid_cache(self):
        """_should_use_cache should return False for invalid cache."""
        # Create invalid cache file
        self.cache_path.write_text("invalid json")

        result = self.orchestrator._should_use_cache(self.cache_path)
        self.assertFalse(result)

    def test_should_use_cache_returns_true_for_valid_cache(self):
        """_should_use_cache should return True for valid cache."""
        # Create valid cache
        analysis_result = self._create_sample_analysis_result()
        self.orchestrator.cache_manager.save_cache(self.cache_path, analysis_result, "abc123", 1)

        result = self.orchestrator._should_use_cache(self.cache_path)
        self.assertTrue(result)

    @patch("static_analyzer.incremental_orchestrator.GitDiffAnalyzer")
    def test_run_incremental_analysis_performs_full_analysis_when_no_cache(self, mock_git_analyzer_class):
        """run_incremental_analysis should perform full analysis when no cache exists."""
        # Setup mocks
        mock_git_analyzer = Mock()
        mock_git_analyzer.get_current_commit.return_value = "abc123"
        mock_git_analyzer_class.return_value = mock_git_analyzer

        analysis_result = self._create_sample_analysis_result()
        self.mock_lsp_client.build_static_analysis.return_value = analysis_result

        # Mock the methods that are called for logging
        self.mock_lsp_client._get_source_files.return_value = [Path("/path/to/file1.py")]
        self.mock_lsp_client.filter_src_files.return_value = [Path("/path/to/file1.py")]

        # Run incremental analysis
        result = self.orchestrator.run_incremental_analysis(self.mock_lsp_client, self.cache_path)

        # Should have called full analysis
        self.mock_lsp_client.build_static_analysis.assert_called_once()

        # Should have saved cache
        self.assertTrue(self.cache_path.exists())

        # Result should match what was returned by LSP client
        self.assertEqual(len(result["call_graph"].nodes), 1)

    @patch("static_analyzer.incremental_orchestrator.GitDiffAnalyzer")
    def test_run_incremental_analysis_uses_cache_when_no_changes(self, mock_git_analyzer_class):
        """run_incremental_analysis should use cache when no changes detected."""
        # Create initial cache
        analysis_result = self._create_sample_analysis_result()
        self.orchestrator.cache_manager.save_cache(self.cache_path, analysis_result, "abc123", 1)

        # Setup mocks - same commit, no uncommitted changes
        mock_git_analyzer = Mock()
        mock_git_analyzer.get_current_commit.return_value = "abc123"
        mock_git_analyzer.has_uncommitted_changes.return_value = False
        mock_git_analyzer_class.return_value = mock_git_analyzer

        # Run incremental analysis
        result = self.orchestrator.run_incremental_analysis(self.mock_lsp_client, self.cache_path)

        # Should NOT have called full analysis
        self.mock_lsp_client.build_static_analysis.assert_not_called()

        # Result should match cached data
        self.assertEqual(len(result["call_graph"].nodes), 1)

    @patch("static_analyzer.incremental_orchestrator.GitDiffAnalyzer")
    def test_run_incremental_analysis_performs_incremental_update_when_changes_exist(self, mock_git_analyzer_class):
        """run_incremental_analysis should perform incremental update when changes exist."""
        # Create initial cache
        analysis_result = self._create_sample_analysis_result()
        self.orchestrator.cache_manager.save_cache(self.cache_path, analysis_result, "abc123", 1)

        # Setup mocks - different commit
        mock_git_analyzer = Mock()
        mock_git_analyzer.get_current_commit.return_value = "def456"
        mock_git_analyzer.get_changed_files.return_value = {Path("/path/to/changed.py")}
        mock_git_analyzer_class.return_value = mock_git_analyzer

        # Mock the LSP client's _analyze_specific_files method
        new_analysis_result = self._create_sample_analysis_result()
        self.mock_lsp_client._analyze_specific_files.return_value = new_analysis_result

        # Run incremental analysis
        result = self.orchestrator.run_incremental_analysis(self.mock_lsp_client, self.cache_path)

        # Should have called get_changed_files
        mock_git_analyzer.get_changed_files.assert_called_once_with("abc123")

        # Should have called _analyze_specific_files on LSP client
        self.mock_lsp_client._analyze_specific_files.assert_called_once_with({Path("/path/to/changed.py")})

        # Result should be merged data
        self.assertIsNotNone(result)

    @patch("static_analyzer.incremental_orchestrator.GitDiffAnalyzer")
    def test_run_incremental_analysis_falls_back_to_full_analysis_on_error(self, mock_git_analyzer_class):
        """run_incremental_analysis should fall back to full analysis on error."""
        # Setup mocks to raise an error
        mock_git_analyzer_class.side_effect = Exception("Git error")

        analysis_result = self._create_sample_analysis_result()
        self.mock_lsp_client.build_static_analysis.return_value = analysis_result

        # Mock the methods that are called for logging
        self.mock_lsp_client._get_source_files.return_value = [Path("/path/to/file1.py")]
        self.mock_lsp_client.filter_src_files.return_value = [Path("/path/to/file1.py")]

        # Run incremental analysis
        result = self.orchestrator.run_incremental_analysis(self.mock_lsp_client, self.cache_path)

        # Should have fallen back to full analysis
        self.mock_lsp_client.build_static_analysis.assert_called_once()

        # Result should match what was returned by LSP client
        self.assertEqual(len(result["call_graph"].nodes), 1)


if __name__ == "__main__":
    unittest.main()
