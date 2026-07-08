"""Warm-start can be scoped git-free from a caller-supplied changed-file set.

The wrapper analyses a frozen, non-git COPY of the working tree, so the git
``get_changed_files_since`` path can't run there. When the caller supplies the
changed files (its fingerprint diff), ``_update_cached_results`` must use them
directly and never touch git.
"""

import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from static_analyzer import EngineConfig, StaticAnalyzer
from static_analyzer.analysis_result import StaticAnalysisResults


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
        self.analyzer = _analyzer_with_one_engine(self.project)
        self.cached = StaticAnalysisResults()

    @patch("static_analyzer.update_cfg_for_changed_files", return_value={})
    @patch("static_analyzer.get_changed_files_since")
    def test_supplied_changed_files_bypass_git(self, mock_git, mock_update) -> None:
        supplied = {self.project / "a.py", self.project / "b.py"}
        with (
            patch.object(self.analyzer, "_extract_language_dict", return_value={}),
            patch.object(self.analyzer, "_absorb_into_results"),
            patch.object(self.analyzer, "_collect_diagnostics_for"),
            patch("static_analyzer.track_lsp_result"),
        ):
            self.analyzer._update_cached_results(self.cached, cached_sha="deadbeef", supplied_changed_files=supplied)

        # Git is never consulted on the supplied path.
        mock_git.assert_not_called()
        # The supplied set (scoped to this engine's project root) reaches the merger.
        passed = (
            mock_update.call_args.args[1]
            if mock_update.call_args.args
            else mock_update.call_args.kwargs["changed_files"]
        )
        self.assertEqual(passed, supplied)

    @patch("static_analyzer.update_cfg_for_changed_files", return_value={})
    @patch("static_analyzer.get_changed_files_since")
    def test_files_outside_project_root_are_scoped_out(self, mock_git, mock_update) -> None:
        inside = self.project / "keep.py"
        outside = Path("/other/repo/skip.py").resolve()
        with (
            patch.object(self.analyzer, "_extract_language_dict", return_value={}),
            patch.object(self.analyzer, "_absorb_into_results"),
            patch.object(self.analyzer, "_collect_diagnostics_for"),
            patch("static_analyzer.track_lsp_result"),
        ):
            self.analyzer._update_cached_results(
                self.cached, cached_sha="deadbeef", supplied_changed_files={inside, outside}
            )
        mock_git.assert_not_called()
        passed = (
            mock_update.call_args.args[1]
            if mock_update.call_args.args
            else mock_update.call_args.kwargs["changed_files"]
        )
        self.assertEqual(passed, {inside})

    @patch("static_analyzer.update_cfg_for_changed_files", return_value={})
    @patch("static_analyzer.get_changed_files_since", return_value={Path("/proj/x.py")})
    def test_none_falls_back_to_git(self, mock_git, mock_update) -> None:
        with (
            patch.object(self.analyzer, "_extract_language_dict", return_value={}),
            patch.object(self.analyzer, "_absorb_into_results"),
            patch.object(self.analyzer, "_collect_diagnostics_for"),
            patch("static_analyzer.track_lsp_result"),
        ):
            self.analyzer._update_cached_results(self.cached, cached_sha="HEAD~1", supplied_changed_files=None)
        # No supplied set => git IS consulted (legacy CLI-on-a-real-checkout path).
        mock_git.assert_called_once()

    @patch("static_analyzer.update_cfg_for_changed_files", return_value={})
    @patch("static_analyzer.get_changed_files_since", side_effect=RuntimeError("Invalid Git repository"))
    def test_git_failure_falls_back_to_full_relsp(self, mock_git, mock_update) -> None:
        # When git fails (non-git dir / bad sha) and nothing was supplied, the
        # language re-LSPs fully rather than crashing.
        with (
            patch.object(self.analyzer, "_extract_language_dict", return_value={}),
            patch.object(self.analyzer, "_run_full_analysis", return_value={}) as mock_full,
            patch.object(self.analyzer, "_absorb_into_results"),
            patch.object(self.analyzer, "_collect_diagnostics_for"),
            patch("static_analyzer.track_lsp_result"),
        ):
            self.analyzer._update_cached_results(self.cached, cached_sha="badsha", supplied_changed_files=None)
        mock_full.assert_called_once()
        mock_update.assert_not_called()


if __name__ == "__main__":
    unittest.main()
