"""Warm-start re-LSPs only files reported by ``get_changed_files_since``."""

import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from static_analyzer import EngineConfig, StaticAnalyzer
from static_analyzer.analysis_result import StaticAnalysisResults
from static_analyzer.program_graph import ProgramGraph


def _analyzer_with_one_engine(project_path: Path) -> StaticAnalyzer:
    analyzer = object.__new__(StaticAnalyzer)
    adapter = MagicMock()
    adapter.language = "Python"
    adapter.language_enum = MagicMock()
    client = MagicMock()
    analyzer._engine_clients = [(EngineConfig(adapter=adapter, project_path=project_path), client)]
    analyzer.collected_diagnostics = {}
    analyzer.ignore_manager = MagicMock()
    analyzer._loc_for_adapter = MagicMock(return_value=0)
    return analyzer


class TestWarmStartChangedFiles(unittest.TestCase):
    def setUp(self) -> None:
        self.project = Path("/proj").resolve()
        self.cached = StaticAnalysisResults()

    @patch("static_analyzer.update_cfg_for_changed_files", return_value={})
    @patch("static_analyzer.get_changed_files_since", return_value={Path("/proj/x.py")})
    def test_warmstart_consults_git_diff(self, mock_git, mock_update) -> None:
        analyzer = _analyzer_with_one_engine(self.project)
        with (
            patch.object(analyzer, "_extract_language_dict", return_value={}),
            patch.object(analyzer, "_absorb_into_results"),
            patch.object(analyzer, "_collect_diagnostics_for"),
            patch("static_analyzer.track_lsp_result"),
        ):
            analyzer._update_cached_results(self.cached, cached_sha="HEAD~1")
        mock_git.assert_called_once()
        mock_update.assert_called_once()

    @patch("static_analyzer.update_cfg_for_changed_files", return_value={})
    @patch("static_analyzer.get_changed_files_since", side_effect=RuntimeError("Invalid Git repository"))
    def test_git_failure_falls_back_to_full_relsp(self, mock_git, mock_update) -> None:
        analyzer = _analyzer_with_one_engine(self.project)
        with (
            patch.object(analyzer, "_extract_language_dict", return_value={}),
            patch.object(analyzer, "_run_full_analysis", return_value={}) as mock_full,
            patch.object(analyzer, "_absorb_into_results"),
            patch.object(analyzer, "_collect_diagnostics_for"),
            patch("static_analyzer.track_lsp_result"),
        ):
            analyzer._update_cached_results(self.cached, cached_sha="badsha")
        mock_full.assert_called_once()
        mock_update.assert_not_called()

    @patch("static_analyzer.get_changed_files_since", return_value={Path("/proj/x.py")})
    def test_program_graph_rebuild_carries_cluster_snapshot(self, _mock_git) -> None:
        analyzer = _analyzer_with_one_engine(self.project)
        language = analyzer._engine_clients[0][0].adapter.language_enum
        old_graph = ProgramGraph(language="python")
        old_graph._cluster_snapshot = {"stable_cluster": [1, 2]}
        cached = StaticAnalysisResults()
        cached.add_program_graph(language, old_graph)
        new_graph = ProgramGraph(language="python")
        analysis = {"program_graph": new_graph, "source_files": []}

        with (
            patch.object(analyzer, "_run_full_analysis", return_value=analysis),
            patch.object(analyzer, "_collect_diagnostics_for"),
            patch("static_analyzer.track_lsp_result"),
        ):
            updated = analyzer._update_cached_results(cached, cached_sha="HEAD~1")

        carried = updated.get_program_graph(language)._cluster_snapshot
        self.assertEqual(carried, old_graph._cluster_snapshot)
        self.assertIsNot(carried, old_graph._cluster_snapshot)


if __name__ == "__main__":
    unittest.main()
