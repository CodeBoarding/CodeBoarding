"""Warm-start scopes supplied or git changes before shared language refreshes."""

import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from static_analyzer import EngineConfig, StaticAnalysisFatalError, StaticAnalyzer
from static_analyzer.analysis_cache import invalidate_files
from static_analyzer.analysis_result import StaticAnalysisResults
from static_analyzer.cfg import CallGraph
from static_analyzer.config import Language
from static_analyzer.engine.adapters.python_adapter import PythonAdapter
from static_analyzer.engine.analysis_context import AnalysisContext
from static_analyzer.incremental_orchestrator import MissingSymbolSnapshotError


def _analyzer_with_one_engine(project_path: Path, changed_files: set[Path] | None) -> StaticAnalyzer:
    analyzer = object.__new__(StaticAnalyzer)
    adapter = PythonAdapter()
    client = MagicMock()
    analyzer._engine_clients = [(EngineConfig(adapter=adapter, project_path=project_path), client)]
    analyzer.collected_diagnostics = {}
    analyzer.ignore_manager = MagicMock()
    analyzer.ignore_manager.should_ignore.return_value = False
    analyzer._loc_for_adapter = MagicMock(return_value=0)
    analyzer.changed_files = changed_files
    return analyzer


class TestWarmStartChangedFiles(unittest.TestCase):
    def setUp(self) -> None:
        self.project = Path("/proj").resolve()
        self.cached = StaticAnalysisResults()
        self.baseline = {
            "call_graph": CallGraph(),
            "class_hierarchies": {},
            "package_relations": {},
            "references": [],
            "source_files": [],
            "symbols": [],
            "unresolved_files": set(),
        }
        self.extract = self.enterContext(
            patch.object(StaticAnalyzer, "_extract_language_dict", return_value=self.baseline)
        )
        self.absorb = self.enterContext(patch.object(StaticAnalyzer, "_absorb_into_results"))
        self.collect = self.enterContext(patch.object(StaticAnalyzer, "_collect_symbols"))
        self.enterContext(patch.object(StaticAnalyzer, "_collect_diagnostics_for"))
        self.enterContext(patch("static_analyzer.track_lsp_result"))
        self.enterContext(patch.object(Path, "is_file", return_value=True))
        self.invalidate = self.enterContext(patch("static_analyzer.invalidate_files", wraps=invalidate_files))
        self.update = self.enterContext(
            patch("static_analyzer.update_cfg_for_changed_files", side_effect=lambda data, *args, **kwargs: data)
        )

    @patch("static_analyzer.get_changed_files_since")
    def test_supplied_changed_files_bypass_git(self, mock_git) -> None:
        supplied = {self.project / "a.py", self.project / "b.py"}
        analyzer = _analyzer_with_one_engine(self.project, changed_files=supplied)
        analyzer._update_cached_results(self.cached, cached_sha="deadbeef")

        mock_git.assert_not_called()
        self.invalidate.assert_called_once_with(self.baseline, supplied)
        self.assertEqual(self.update.call_args.args[1], set())
        self.assertEqual(self.update.call_args.kwargs["query_files"], supplied)
        self.assertIs(self.update.call_args.kwargs["context"], analyzer._analysis_context)

    @patch("static_analyzer.get_changed_files_since")
    def test_files_outside_project_root_are_scoped_out(self, mock_git) -> None:
        inside = self.project / "keep.py"
        outside = Path("/other/repo/skip.py").resolve()
        analyzer = _analyzer_with_one_engine(self.project, changed_files={inside, outside})
        analyzer._update_cached_results(self.cached, cached_sha="deadbeef")

        mock_git.assert_not_called()
        self.invalidate.assert_called_once_with(self.baseline, {inside})
        self.assertEqual(self.update.call_args.kwargs["query_files"], {inside})

    @patch("static_analyzer.get_changed_files_since")
    def test_engine_configs_of_one_language_thread_one_dict_and_absorb_once(self, mock_git) -> None:
        """Why: later engines must not restore declarations invalidated by earlier engines."""
        outer_file = self.project / "a.py"
        inner_root = self.project / "sub"
        inner_file = inner_root / "b.py"
        changes = {outer_file, inner_file}
        analyzer = _analyzer_with_one_engine(self.project, changed_files=changes)
        ((config, client),) = analyzer._engine_clients
        config.excluded_roots = [inner_root]
        analyzer._engine_clients.append((EngineConfig(config.adapter, inner_root), client))
        first, second = dict(self.baseline), dict(self.baseline)

        def collect(config, client):
            self.invalidate.assert_called_once_with(self.baseline, changes)
            self.update.assert_not_called()

        def update(data, *args, **kwargs):
            self.assertEqual(self.collect.call_count, 2)
            return first if self.update.call_count == 1 else second

        self.collect.side_effect = collect
        self.update.side_effect = update
        with patch.object(AnalysisContext, "hydrate", autospec=True, side_effect=AnalysisContext.hydrate) as hydrate:
            analyzer._update_cached_results(self.cached, cached_sha="deadbeef")

        mock_git.assert_not_called()
        self.extract.assert_called_once_with(self.cached, Language.PYTHON)
        hydrate.assert_called_once_with(analyzer._analysis_context, config.adapter, [], set(), set())
        self.assertEqual(
            [call.args[0].source_files for call in self.collect.call_args_list], [[outer_file], [inner_file]]
        )
        self.assertIs(self.update.call_args_list[1].args[0], first)
        for call, expected in zip(self.update.call_args_list, [{outer_file}, {inner_file}]):
            self.assertEqual(call.args[1], set())
            self.assertIs(call.kwargs["context"], analyzer._analysis_context)
            self.assertEqual(call.kwargs["query_files"], expected)
        self.absorb.assert_called_once()
        self.assertIs(self.absorb.call_args.args[2], second)

    def test_discovery_exclusions_scope_supplied_and_git_changes_before_invalidation(self) -> None:
        inner_root = self.project / "plugins"
        excluded = inner_root / "Inner"
        inner_changes = {excluded / name for name in ("modified.py", "added.py", "deleted.py")}
        outer_changes = {self.project / "Outer.py", self.project / "plugins-extra" / "Other.py"}
        all_changes = inner_changes | outer_changes
        deleted = excluded / "deleted.py"
        for use_git in (False, True):
            with self.subTest(use_git=use_git):
                self.invalidate.reset_mock()
                self.update.reset_mock()
                self.collect.reset_mock()
                analyzer = _analyzer_with_one_engine(self.project, changed_files=None if use_git else all_changes)
                ((outer, client),) = analyzer._engine_clients
                outer.excluded_roots = [excluded]
                outer.source_files = [self.project / "Unchanged.py"]
                inner = EngineConfig(outer.adapter, inner_root, source_files=[excluded / "modified.py"])
                analyzer._engine_clients.append((inner, client))
                with (
                    patch("static_analyzer.get_changed_files_since", side_effect=[all_changes, inner_changes]) as git,
                    patch.object(Path, "is_file", new=lambda path: path != deleted),
                ):
                    analyzer._update_cached_results(self.cached, cached_sha="baseline")

                self.assertEqual(git.call_count, 2 if use_git else 0)
                self.invalidate.assert_called_once_with(self.baseline, all_changes)
                self.assertEqual(self.update.call_args_list[0].kwargs["query_files"], outer_changes)
                self.assertEqual(self.update.call_args_list[1].kwargs["query_files"], inner_changes - {deleted})
                self.assertEqual(set(self.collect.call_args_list[0].args[0].source_files), outer_changes)
                self.assertEqual(set(self.collect.call_args_list[1].args[0].source_files), inner_changes - {deleted})

    @patch("static_analyzer.get_changed_files_since", return_value={Path("/proj/x.py")})
    def test_none_falls_back_to_git(self, mock_git) -> None:
        analyzer = _analyzer_with_one_engine(self.project, changed_files=None)
        analyzer._update_cached_results(self.cached, cached_sha="HEAD~1")

        mock_git.assert_called_once_with(self.project, "HEAD~1")
        self.invalidate.assert_called_once_with(self.baseline, {self.project / "x.py"})
        self.assertEqual(self.update.call_args.kwargs["query_files"], {self.project / "x.py"})

    @patch("static_analyzer.get_changed_files_since", side_effect=RuntimeError("Invalid Git repository"))
    def test_git_failure_fails_loud_without_full_analysis(self, mock_git) -> None:
        analyzer = _analyzer_with_one_engine(self.project, changed_files=None)
        with patch.object(analyzer, "_run_full_analysis") as full:
            with self.assertRaisesRegex(StaticAnalysisFatalError, "Cannot determine incremental changes"):
                analyzer._update_cached_results(self.cached, cached_sha="badsha")

        mock_git.assert_called_once_with(self.project, "badsha")
        full.assert_not_called()
        self.invalidate.assert_not_called()
        self.collect.assert_not_called()
        self.update.assert_not_called()
        self.absorb.assert_not_called()

    @patch("static_analyzer.get_changed_files_since")
    def test_missing_symbol_snapshot_fails_loud(self, mock_git) -> None:
        self.baseline.pop("symbols")
        analyzer = _analyzer_with_one_engine(self.project, changed_files=set())
        with patch.object(analyzer, "_run_full_analysis") as full:
            with self.assertRaisesRegex(MissingSymbolSnapshotError, "no symbol snapshot"):
                analyzer._update_cached_results(self.cached, cached_sha="baseline")

        mock_git.assert_not_called()
        full.assert_not_called()
        self.invalidate.assert_not_called()
        self.collect.assert_not_called()
        self.update.assert_not_called()
        self.absorb.assert_not_called()


if __name__ == "__main__":
    unittest.main()
