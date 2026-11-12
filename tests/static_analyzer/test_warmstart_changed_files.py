"""Warm-start scoping never asks git.

The changed-file set is every file whose content hash differs from the hashes the pkl
recorded, plus whatever the caller supplies (the incremental fingerprint diff). Without
recorded hashes only the caller's set is known, and none at all rebuilds every language.
This is what lets a full run reuse the pkl in a non-git frozen copy or a shallow CI clone.
"""

import pickle
import unittest
from pathlib import Path
from typing import cast
from unittest.mock import MagicMock, patch

from static_analyzer import EngineConfig, StaticAnalyzer
from static_analyzer.analysis_result import StaticAnalysisResults
from static_analyzer.lsp_client.diagnostics import LSPDiagnostic


def _analyzer_with_one_engine(project_path: Path, changed_files: set[Path] | None) -> StaticAnalyzer:
    analyzer = object.__new__(StaticAnalyzer)
    analyzer.repository_path = project_path
    analyzer._server_slots = 0
    adapter = MagicMock()
    adapter.language = "Python"
    adapter.language_enum = MagicMock()
    adapter.rebuilds_for.return_value = False
    client = MagicMock()
    analyzer._engine_clients = [(EngineConfig(adapter=adapter, project_path=project_path), client)]
    analyzer.collected_diagnostics = {}
    analyzer.ignore_manager = MagicMock()
    analyzer._loc_for_adapter = MagicMock(return_value=0)
    analyzer.changed_files = changed_files
    analyzer._clients_started = True
    analyzer._cached_results = None
    return analyzer


class TestAnalyzeScopesTheWarmStart(unittest.TestCase):
    """Which changed-file set ``analyze`` hands the warm start, and what it records for the next one."""

    def setUp(self) -> None:
        self.project = Path("/proj").resolve()
        self.live = {"a.py": "1", "b.py": "2", "new.py": "3"}

    def _analyze(
        self, analyzer: StaticAnalyzer, recorded: dict[str, str] | None
    ) -> tuple[MagicMock, StaticAnalysisResults]:
        cached = StaticAnalysisResults(source_file_hashes=recorded)
        cache = MagicMock()
        cache.load_with_sha.return_value = (cached, "sha")
        updated = StaticAnalysisResults()
        with (
            patch("static_analyzer.StaticAnalysisCache", return_value=cache),
            patch.object(analyzer, "_update_cached_results", return_value=updated) as update,
            patch.object(analyzer, "_validate_analysis_results"),
        ):
            results = analyzer.analyze(cache_dir=Path("/out"), source_file_hashes=self.live)
        return update, results

    def test_a_full_run_re_analyses_the_files_whose_hash_differs(self) -> None:
        analyzer = _analyzer_with_one_engine(self.project, changed_files=None)
        update, results = self._analyze(analyzer, recorded={"a.py": "1", "b.py": "old", "gone.py": "4"})

        changed = update.call_args.args[1]
        self.assertEqual(changed, {self.project / name for name in ("b.py", "new.py", "gone.py")})
        self.assertEqual(results.source_file_hashes, self.live)

    def test_a_supplied_set_adds_to_what_the_recorded_hashes_show(self) -> None:
        """Why: a partial run supplies no changes, yet the pkl it saves must still match the tree."""
        analyzer = _analyzer_with_one_engine(self.project, changed_files={self.project / "a.py"})
        update, results = self._analyze(analyzer, recorded={"a.py": "1", "b.py": "old", "new.py": "3"})

        self.assertEqual(update.call_args.args[1], {self.project / "a.py", self.project / "b.py"})
        self.assertEqual(results.source_file_hashes, self.live)

    def test_a_supplied_set_without_recorded_hashes_records_none(self) -> None:
        """Why: only the supplied files were refreshed, so the pkl cannot claim the whole tree."""
        supplied = {self.project / "a.py"}
        analyzer = _analyzer_with_one_engine(self.project, changed_files=supplied)
        update, results = self._analyze(analyzer, recorded=None)

        self.assertEqual(update.call_args.args[1], supplied)
        self.assertIsNone(results.source_file_hashes)

    def test_a_pkl_without_recorded_hashes_rebuilds_every_language(self) -> None:
        analyzer = _analyzer_with_one_engine(self.project, changed_files=None)
        update, results = self._analyze(analyzer, recorded=None)

        self.assertIsNone(update.call_args.args[1])
        self.assertEqual(results.source_file_hashes, self.live)

    def test_a_pickle_saved_before_hashes_were_recorded_reads_none(self) -> None:
        old = StaticAnalysisResults()
        del old.__dict__["source_file_hashes"]
        self.assertIsNone(pickle.loads(pickle.dumps(old)).source_file_hashes)


class TestWarmStartChangedFiles(unittest.TestCase):
    def setUp(self) -> None:
        self.project = Path("/proj").resolve()
        self.cached = StaticAnalysisResults()

    @patch("static_analyzer.update_cfg_for_changed_files", return_value={})
    def test_files_outside_project_root_are_scoped_out(self, mock_update) -> None:
        inside = self.project / "keep.py"
        outside = Path("/other/repo/skip.py").resolve()
        analyzer = _analyzer_with_one_engine(self.project, changed_files={inside, outside})
        with (
            patch.object(analyzer, "_extract_language_dict", return_value={}),
            patch.object(analyzer, "_absorb_into_results"),
            patch.object(analyzer, "_collect_diagnostics_for"),
            patch("static_analyzer.track_lsp_result"),
        ):
            analyzer._update_cached_results(self.cached, analyzer.changed_files)
        self.assertEqual(mock_update.call_args.args[1], {inside})

    def test_engine_configs_of_one_language_thread_one_dict_and_absorb_once(self) -> None:
        """Why: a config that copies the cache hands back the nodes another config invalidated."""
        analyzer = _analyzer_with_one_engine(self.project, changed_files={self.project / "sub" / "a.py"})
        ((config, client),) = analyzer._engine_clients
        analyzer._engine_clients.append(
            (EngineConfig(adapter=config.adapter, project_path=self.project / "sub"), client)
        )
        first, second = {"call_graph": "first"}, {"call_graph": "second"}
        with (
            patch("static_analyzer.update_cfg_for_changed_files", side_effect=[first, second]) as mock_update,
            patch.object(analyzer, "_extract_language_dict", return_value={"call_graph": "cached"}) as extract,
            patch.object(analyzer, "_absorb_into_results") as absorb,
            patch.object(analyzer, "_collect_diagnostics_for"),
            patch("static_analyzer.track_lsp_result"),
        ):
            analyzer._update_cached_results(self.cached, analyzer.changed_files)
        extract.assert_called_once()
        self.assertIs(mock_update.call_args_list[1].args[0], first)
        absorb.assert_called_once()
        self.assertIs(absorb.call_args.args[2], second)

    def test_discovery_exclusions_scope_changes_before_invalidation(self) -> None:
        inner_root = self.project / "plugins"
        excluded = inner_root / "Inner"
        inner_changes = {excluded / name for name in ("modified.cs", "added.cs", "deleted.cs")}
        outer_changes = {self.project / "Outer.cs", self.project / "plugins-extra" / "Other.cs"}
        analyzer = _analyzer_with_one_engine(self.project, changed_files=inner_changes | outer_changes)
        ((outer, client),) = analyzer._engine_clients
        outer.excluded_roots = [excluded]
        outer.source_files = [self.project / "Unchanged.cs"]
        inner = EngineConfig(outer.adapter, inner_root, source_files=[excluded / "modified.cs"])
        analyzer._engine_clients.append((inner, client))
        cached = {"source_files": outer.source_files + inner.source_files}
        with (
            patch("static_analyzer.update_cfg_for_changed_files", side_effect=[cached, cached]) as update,
            patch.object(analyzer, "_extract_language_dict", return_value=cached),
            patch.object(analyzer, "_absorb_into_results"),
            patch.object(analyzer, "_collect_diagnostics_for"),
            patch("static_analyzer.track_lsp_result"),
        ):
            analyzer._update_cached_results(self.cached, analyzer.changed_files)
        self.assertEqual(update.call_args_list[0].args[1], outer_changes)
        # Added/deleted paths are absent from source_files but still belong to the inner engine.
        self.assertEqual(update.call_args_list[1].args[1], inner_changes)

    @patch("static_analyzer.update_cfg_for_changed_files", return_value={})
    def test_unknown_changes_re_analyse_the_language_in_full(self, mock_update) -> None:
        analyzer = _analyzer_with_one_engine(self.project, changed_files=None)
        with (
            patch.object(analyzer, "_extract_language_dict", return_value={}),
            patch.object(analyzer, "_run_full_analysis", return_value={}) as mock_full,
            patch.object(analyzer, "_absorb_into_results"),
            patch.object(analyzer, "_collect_diagnostics_for"),
            patch("static_analyzer.track_lsp_result"),
        ):
            analyzer._update_cached_results(self.cached, None)
        mock_full.assert_called_once()
        mock_update.assert_not_called()

    @patch("static_analyzer.update_cfg_for_changed_files")
    def test_a_language_with_no_changes_keeps_its_cache_without_a_server(self, mock_update) -> None:
        analyzer = _analyzer_with_one_engine(self.project, changed_files=set())
        analyzer._server_slots = 1
        ((config, _),) = analyzer._engine_clients
        config.source_files = [self.project / "a.py"]
        analyzer._engine_configs = [config]
        cached = {"source_files": [self.project / "a.py"], "diagnostics": {"/proj/a.py": ["warning"]}}
        with (
            patch.object(analyzer, "_spawn_engine_client") as spawn,
            patch.object(analyzer, "_extract_language_dict", return_value=cached),
            patch.object(analyzer, "_absorb_into_results") as absorb,
        ):
            analyzer._update_cached_results(self.cached, set())
        spawn.assert_not_called()
        mock_update.assert_not_called()
        self.assertIs(absorb.call_args.args[2], cached)
        self.assertEqual(analyzer.collected_diagnostics[config.adapter.results_language], {"/proj/a.py": ["warning"]})

    def test_a_config_without_changes_keeps_the_diagnostics_a_sibling_config_merged(self) -> None:
        """Why: the carried snapshot predates the sibling's wait for late diagnostics."""
        analyzer = _analyzer_with_one_engine(self.project, changed_files={self.project / "a.py"})
        ((config, client),) = analyzer._engine_clients
        sibling = EngineConfig(adapter=config.adapter, project_path=self.project / "nested")
        analyzer._engine_clients.append((sibling, client))
        language = config.adapter.results_language
        early, late = LSPDiagnostic(message="early"), LSPDiagnostic(message="late")
        snapshot = {"source_files": [], "diagnostics": {"/proj/a.py": [early]}}

        def merge(_adapter, _client, _analysis) -> None:
            analyzer.collected_diagnostics.setdefault(language, {})["/proj/a.py"] = [early, late]

        with (
            patch("static_analyzer.update_cfg_for_changed_files", return_value=snapshot),
            patch.object(analyzer, "_extract_language_dict", return_value=snapshot),
            patch.object(analyzer, "_absorb_into_results"),
            patch.object(analyzer, "_collect_diagnostics_for", side_effect=merge),
            patch("static_analyzer.track_lsp_result"),
        ):
            analyzer._update_cached_results(self.cached, analyzer.changed_files)
        self.assertEqual(analyzer.collected_diagnostics[language], {"/proj/a.py": [early, late]})

    @patch("static_analyzer.update_cfg_for_changed_files")
    def test_a_config_the_pkl_holds_nothing_of_is_rebuilt_even_without_changes(self, mock_update) -> None:
        """Why: its server failed last run, so no change to its files would ever bring it back."""
        analyzer = _analyzer_with_one_engine(self.project, changed_files=set())
        ((config, client),) = analyzer._engine_clients
        config.source_files = [self.project / "a.py"]
        with (
            patch.object(analyzer, "_extract_language_dict", return_value={"source_files": []}),
            patch.object(analyzer, "_run_full_analysis", return_value={}) as mock_full,
            patch.object(analyzer, "_absorb_into_results"),
            patch.object(analyzer, "_collect_diagnostics_for"),
            patch("static_analyzer.track_lsp_result"),
        ):
            analyzer._update_cached_results(self.cached, set())
        mock_full.assert_called_once_with(config, client)
        mock_update.assert_not_called()

    @patch("static_analyzer.update_cfg_for_changed_files", return_value={})
    def test_a_change_that_alters_how_imports_resolve_rebuilds_on_a_fresh_server(self, mock_update) -> None:
        analyzer = _analyzer_with_one_engine(self.project, changed_files={self.project / "pnpm-workspace.yaml"})
        [(config, stale)] = analyzer._engine_clients
        adapter, stale_client = cast(MagicMock, config.adapter), cast(MagicMock, stale)
        adapter.rebuilds_for.return_value = True
        stale_client.shutdown.side_effect = lambda: setattr(stale_client, "alive", False)
        fresh = MagicMock()
        with (
            patch.object(analyzer, "_spawn_engine_client", return_value=fresh),
            patch.object(analyzer, "_run_full_analysis", return_value={}) as mock_full,
            patch.object(analyzer, "_absorb_into_results"),
            patch.object(analyzer, "_collect_diagnostics_for"),
            patch("static_analyzer.track_lsp_result"),
        ):
            analyzer._update_cached_results(self.cached, analyzer.changed_files)

        stale_client.shutdown.assert_called()
        mock_full.assert_called_once_with(config, fresh)
        mock_update.assert_not_called()
        self.assertEqual(analyzer._engine_clients, [(config, fresh)])


if __name__ == "__main__":
    unittest.main()
