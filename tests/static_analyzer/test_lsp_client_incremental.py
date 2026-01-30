"""Tests for LSP client incremental analysis capabilities."""

import tempfile
import shutil
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from static_analyzer.lsp_client.client import LSPClient
from static_analyzer.graph import CallGraph, Node
from static_analyzer.programming_language import ProgrammingLanguage
from repo_utils.ignore import RepoIgnoreManager


class MockLSPClient(LSPClient):
    """Mock LSP client for testing incremental capabilities."""

    def __init__(self, project_path: Path):
        # Initialize with minimal setup for testing
        self.project_path = project_path
        self.language = ProgrammingLanguage(
            language="Python", size=1000, percentage=100.0, suffixes=[".py"], server_commands=["python", "-m", "pylsp"]
        )
        self.call_graph = CallGraph()
        self.ignore_manager = RepoIgnoreManager(project_path)

    def _get_source_files(self):
        """Override to return test files."""
        return [self.project_path / "file1.py", self.project_path / "file2.py", self.project_path / "file3.py"]

    def filter_src_files(self, src_files):
        """Override to return filtered files."""
        return [f for f in src_files if f.name != "ignored.py"]

    def build_static_analysis(self):
        """Override to return mock analysis result."""
        call_graph = CallGraph()
        node1 = Node("module.function1", 6, str(self.project_path / "file1.py"), 10, 20)
        call_graph.add_node(node1)

        return {
            "call_graph": call_graph,
            "class_hierarchies": {},
            "package_relations": {},
            "references": [node1],
            "source_files": [self.project_path / "file1.py"],
        }


class TestLSPClientIncremental(unittest.TestCase):
    """Tests for LSP client incremental analysis methods."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.project_path = Path(self.temp_dir)
        self.lsp_client = MockLSPClient(self.project_path)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_analyze_specific_files_filters_to_relevant_files(self):
        """_analyze_specific_files should only analyze files that are in the source file set."""
        # Files to analyze include both relevant and irrelevant files
        files_to_analyze = {self.project_path / "file1.py", self.project_path / "irrelevant.py"}

        # Run analysis
        result = self.lsp_client._analyze_specific_files(files_to_analyze)

        # Should return analysis result
        self.assertIsNotNone(result)
        self.assertIn("call_graph", result)
        self.assertIn("class_hierarchies", result)
        self.assertIn("package_relations", result)
        self.assertIn("references", result)
        self.assertIn("source_files", result)

    def test_analyze_specific_files_returns_empty_result_when_no_relevant_files(self):
        """_analyze_specific_files should return empty result when no relevant files to analyze."""
        # Files to analyze don't overlap with relevant files
        files_to_analyze = {self.project_path / "irrelevant.py"}

        # Run analysis
        result = self.lsp_client._analyze_specific_files(files_to_analyze)

        # Should return empty result
        self.assertEqual(len(result["call_graph"].nodes), 0)
        self.assertEqual(len(result["class_hierarchies"]), 0)
        self.assertEqual(len(result["package_relations"]), 0)
        self.assertEqual(len(result["references"]), 0)
        self.assertEqual(len(result["source_files"]), 0)

    def test_analyze_specific_files_logs_progress(self):
        """_analyze_specific_files should log progress information."""
        files_to_analyze = {self.project_path / "file1.py"}

        with patch("static_analyzer.lsp_client.client.logger") as mock_logger:
            result = self.lsp_client._analyze_specific_files(files_to_analyze)

            # Should have logged progress
            mock_logger.info.assert_called()

            # Check that it logged the number of files being analyzed
            log_calls = [call.args[0] for call in mock_logger.info.call_args_list]
            self.assertTrue(any("Analyzing" in call and "specific files" in call for call in log_calls))

    @patch("static_analyzer.incremental_orchestrator.IncrementalAnalysisOrchestrator")
    def test_build_incremental_analysis_uses_orchestrator(self, mock_orchestrator_class):
        """build_incremental_analysis should use IncrementalAnalysisOrchestrator."""
        mock_orchestrator = Mock()
        mock_orchestrator.run_incremental_analysis.return_value = {
            "call_graph": CallGraph(),
            "class_hierarchies": {},
            "package_relations": {},
            "references": [],
            "source_files": [],
        }
        mock_orchestrator_class.return_value = mock_orchestrator

        cache_path = Path(self.temp_dir) / "cache.json"

        # Run incremental analysis
        result = self.lsp_client.build_incremental_analysis(cache_path)

        # Should have created orchestrator and called run_incremental_analysis
        mock_orchestrator_class.assert_called_once()
        mock_orchestrator.run_incremental_analysis.assert_called_once_with(self.lsp_client, cache_path)

        # Should return result
        self.assertIsNotNone(result)

    @patch("static_analyzer.incremental_orchestrator.IncrementalAnalysisOrchestrator")
    def test_build_incremental_analysis_logs_statistics(self, mock_orchestrator_class):
        """build_incremental_analysis should log final statistics."""
        call_graph = CallGraph()
        node1 = Node("module.function1", 6, "/path/to/file1.py", 10, 20)
        call_graph.add_node(node1)

        mock_orchestrator = Mock()
        mock_orchestrator.run_incremental_analysis.return_value = {
            "call_graph": call_graph,
            "class_hierarchies": {"Class1": {}},
            "package_relations": {"package1": {}},
            "references": [node1],
            "source_files": [Path("/path/to/file1.py")],
        }
        mock_orchestrator_class.return_value = mock_orchestrator

        cache_path = Path(self.temp_dir) / "cache.json"

        with patch("static_analyzer.lsp_client.client.logger") as mock_logger:
            result = self.lsp_client.build_incremental_analysis(cache_path)

            # Should have logged final statistics
            mock_logger.info.assert_called()

            # Check that it logged statistics
            log_calls = [call.args[0] for call in mock_logger.info.call_args_list]
            self.assertTrue(any("Incremental analysis complete" in call for call in log_calls))


if __name__ == "__main__":
    unittest.main()
