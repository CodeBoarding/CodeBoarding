"""ProgramGraph warm-start behavior, including git-free caller-supplied change sets.

The wrapper analyses a frozen, non-git COPY of the working tree, so the git
``get_changed_files_since`` path can't run there. When the analyzer is built with
a changed-file set (its fingerprint diff), ``_update_cached_results`` must use it
directly and never touch git.
"""

import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from static_analyzer import EngineConfig, IncrementalProgramGraphUnavailableError, StaticAnalyzer
from static_analyzer.analysis_result import StaticAnalysisResults
from static_analyzer.program_graph import ProgramGraph


def _analyzer_with_one_engine(project_path: Path, changed_files: set[Path] | None = None) -> StaticAnalyzer:
    analyzer = object.__new__(StaticAnalyzer)
    adapter = MagicMock()
    adapter.language = "Python"
    adapter.language_enum = MagicMock()
    adapter.file_extensions = {".py"}
    analyzer._engine_clients = [(EngineConfig(adapter=adapter, project_path=project_path), MagicMock())]
    analyzer.collected_diagnostics = {}
    analyzer.ignore_manager = MagicMock()
    analyzer.changed_files = changed_files
    return analyzer


def _cached_graph(analyzer: StaticAnalyzer) -> StaticAnalysisResults:
    language = analyzer._engine_clients[0][0].adapter.language_enum
    cached = StaticAnalysisResults()
    cached.add_program_graph(language, ProgramGraph(language="python"))
    return cached


class TestWarmStartChangedFiles(unittest.TestCase):
    def setUp(self) -> None:
        self.project = Path("/proj").resolve()

    def test_missing_program_graph_baseline_raises(self) -> None:
        analyzer = _analyzer_with_one_engine(self.project)

        with self.assertRaisesRegex(IncrementalProgramGraphUnavailableError, "run a full analysis first"):
            analyzer._update_cached_results(StaticAnalysisResults(), cached_sha="HEAD~1")

    @patch("static_analyzer.get_changed_files_since", return_value={Path("/proj/x.py")})
    def test_changed_files_are_spliced_from_a_scoped_analysis(self, mock_git) -> None:
        analyzer = _analyzer_with_one_engine(self.project)
        delta_graph = ProgramGraph(language="python")

        with (
            patch.object(analyzer, "_source_files_for_config", return_value=[Path("/proj/x.py")]),
            patch.object(analyzer, "_incremental_scope_files", return_value=[Path("/proj/x.py")]),
            patch.object(analyzer, "_run_analysis_for_files", return_value={"program_graph": delta_graph}) as run_delta,
            patch.object(analyzer, "_merge_incremental_diagnostics"),
        ):
            updated = analyzer._update_cached_results(_cached_graph(analyzer), cached_sha="HEAD~1")

        mock_git.assert_called_once()
        run_delta.assert_called_once()
        self.assertEqual(
            updated.get_program_graph(analyzer._engine_clients[0][0].adapter.language_enum).language,
            "python",
        )

    @patch("static_analyzer.get_changed_files_since")
    def test_supplied_changed_files_bypass_git(self, mock_git) -> None:
        supplied = {self.project / "a.py", self.project / "b.py"}
        analyzer = _analyzer_with_one_engine(self.project, changed_files=supplied)

        with (
            patch.object(analyzer, "_source_files_for_config", return_value=sorted(supplied)),
            patch.object(analyzer, "_incremental_scope_files", return_value=[]) as scope,
            patch.object(analyzer, "_merge_incremental_diagnostics"),
        ):
            analyzer._update_cached_results(_cached_graph(analyzer), cached_sha="deadbeef")

        mock_git.assert_not_called()
        self.assertEqual(scope.call_args.args[1], supplied)

    @patch("static_analyzer.get_changed_files_since")
    def test_files_outside_project_root_are_scoped_out(self, mock_git) -> None:
        inside = self.project / "keep.py"
        outside = Path("/other/repo/skip.py").resolve()
        analyzer = _analyzer_with_one_engine(self.project, changed_files={inside, outside})

        with (
            patch.object(analyzer, "_source_files_for_config", return_value=[inside]),
            patch.object(analyzer, "_incremental_scope_files", return_value=[]) as scope,
            patch.object(analyzer, "_merge_incremental_diagnostics"),
        ):
            analyzer._update_cached_results(_cached_graph(analyzer), cached_sha="deadbeef")

        mock_git.assert_not_called()
        self.assertEqual(scope.call_args.args[1], {inside})

    @patch("static_analyzer.get_changed_files_since", return_value={Path("/proj/x.py")})
    def test_none_falls_back_to_git(self, mock_git) -> None:
        analyzer = _analyzer_with_one_engine(self.project, changed_files=None)

        with (
            patch.object(analyzer, "_source_files_for_config", return_value=[Path("/proj/x.py")]),
            patch.object(analyzer, "_incremental_scope_files", return_value=[]),
            patch.object(analyzer, "_merge_incremental_diagnostics"),
        ):
            analyzer._update_cached_results(_cached_graph(analyzer), cached_sha="HEAD~1")

        mock_git.assert_called_once()

    @patch("static_analyzer.get_changed_files_since", side_effect=RuntimeError("Invalid Git repository"))
    def test_git_failure_raises(self, mock_git) -> None:
        # Why: incremental must never silently degrade into a full re-analysis —
        # an unusable change set is a loud failure the caller has to act on.
        analyzer = _analyzer_with_one_engine(self.project, changed_files=None)

        with self.assertRaisesRegex(IncrementalProgramGraphUnavailableError, "No changed-file set"):
            analyzer._update_cached_results(_cached_graph(analyzer), cached_sha="badsha")

        mock_git.assert_called_once()

    @patch("static_analyzer.get_changed_files_since", return_value=set())
    def test_unchanged_graph_is_reused(self, _mock_git) -> None:
        analyzer = _analyzer_with_one_engine(self.project)
        cached = _cached_graph(analyzer)
        language = analyzer._engine_clients[0][0].adapter.language_enum

        updated = analyzer._update_cached_results(cached, cached_sha="HEAD")

        self.assertIsNot(updated.get_program_graph(language), cached.get_program_graph(language))


if __name__ == "__main__":
    unittest.main()
