"""A warm start starts only the deferred engines whose files changed, each with only its own files.

Why it matters: one tsserver per package means dozens of engines of one language sharing the
repository root, so scoping changes by root would start every server with every change.
"""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from static_analyzer import EngineConfig, StaticAnalysisFatalError, StaticAnalyzer
from static_analyzer.analysis_result import StaticAnalysisResults
from static_analyzer.cfg import CallGraph
from static_analyzer.config import Language, NodeType
from static_analyzer.node import Node


def _adapter() -> MagicMock:
    adapter = MagicMock()
    adapter.language = "TypeScript"
    adapter.language_enum = Language.TYPESCRIPT
    adapter.results_language = Language.TYPESCRIPT
    adapter.file_extensions = (".ts", ".tsx")
    return adapter


class TestDeferredWarmStart:
    @pytest.fixture
    def repo(self, tmp_path: Path) -> Path:
        for rel in ("packages/a/src/a.ts", "packages/b/src/b.ts", "scripts/build.ts"):
            (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
            (tmp_path / rel).write_text("export {};\n")
        return tmp_path.resolve()

    def _analyzer(self, repo: Path, changed: set[Path]) -> tuple[StaticAnalyzer, dict[str, EngineConfig]]:
        analyzer = StaticAnalyzer.__new__(StaticAnalyzer)
        analyzer.repository_path = repo
        analyzer.changed_files = changed
        analyzer.collected_diagnostics = {}
        analyzer.ignore_manager = MagicMock()
        analyzer._loc_for_adapter = MagicMock(return_value=0)
        adapter = _adapter()
        engines = {
            "a": EngineConfig(
                adapter, repo, [repo / "packages/a/src/a.ts"], deferred=True, files_root=repo / "packages/a"
            ),
            "b": EngineConfig(
                adapter, repo, [repo / "packages/b/src/b.ts"], deferred=True, files_root=repo / "packages/b"
            ),
            "rest": EngineConfig(adapter, repo, [repo / "scripts/build.ts"], deferred=True),
        }
        analyzer._engine_configs = list(engines.values())
        analyzer._engine_clients = []
        return analyzer, engines

    def _run(self, analyzer: StaticAnalyzer, spawn: MagicMock) -> MagicMock:
        with (
            patch.dict("os.environ", {}, clear=True),
            patch.object(analyzer, "_spawn_engine_client", spawn),
            patch("static_analyzer.update_cfg_for_changed_files", return_value={}) as update,
            patch.object(analyzer, "_extract_language_dict", return_value={}),
            patch.object(analyzer, "_absorb_into_results"),
            patch.object(analyzer, "_collect_diagnostics_for"),
            patch("static_analyzer.track_lsp_result"),
        ):
            analyzer._update_cached_results(MagicMock(), cached_sha="baseline")
        return update

    def test_only_the_engine_whose_file_changed_starts(self, repo: Path):
        changed = {repo / "packages/a/src/a.ts"}
        analyzer, engines = self._analyzer(repo, changed)
        client = MagicMock()
        spawn = MagicMock(return_value=client)
        update = self._run(analyzer, spawn)
        spawn.assert_called_once_with(engines["a"])
        assert update.call_count == 1
        assert update.call_args.args[1] == changed
        client.shutdown.assert_called_once()

    def test_a_new_file_goes_to_the_engine_whose_directory_holds_it_most_closely(self, repo: Path):
        new = repo / "packages/b/src/new.ts"
        analyzer, engines = self._analyzer(repo, {new})
        spawn = MagicMock(return_value=MagicMock())
        update = self._run(analyzer, spawn)
        spawn.assert_called_once_with(engines["b"])
        assert update.call_args.args[1] == {new}

    def test_a_new_file_outside_every_package_goes_to_the_repository_engine(self, repo: Path):
        new = repo / "tools/release.ts"
        analyzer, engines = self._analyzer(repo, {new})
        spawn = MagicMock(return_value=MagicMock())
        self._run(analyzer, spawn)
        spawn.assert_called_once_with(engines["rest"])

    def test_a_file_of_another_language_starts_nothing(self, repo: Path):
        analyzer, _engines = self._analyzer(repo, {repo / "packages/a/tool.py"})
        spawn = MagicMock()
        update = self._run(analyzer, spawn)
        spawn.assert_not_called()
        update.assert_not_called()

    def test_without_a_change_list_an_engine_re_reads_its_own_files(self, repo: Path):
        analyzer, engines = self._analyzer(repo, set())
        analyzer.changed_files = None
        spawn = MagicMock(return_value=MagicMock())
        with patch("static_analyzer.get_changed_files_since", side_effect=RuntimeError("not a git repository")):
            update = self._run(analyzer, spawn)
        assert spawn.call_count == 3
        assert [call.args[1] for call in update.call_args_list] == [
            set(engines["a"].source_files),
            set(engines["b"].source_files),
            set(engines["rest"].source_files),
        ]

    def test_a_server_that_will_not_start_keeps_the_cache(self, repo: Path):
        analyzer, _engines = self._analyzer(repo, {repo / "packages/a/src/a.ts"})
        spawn = MagicMock(side_effect=RuntimeError("tsserver missing"))
        with pytest.raises(StaticAnalysisFatalError, match="Keeping the cache"):
            self._run(analyzer, spawn)

    def _cached(self, repo: Path) -> StaticAnalysisResults:
        """A cache holding package a and a package deleted since, which calls into a."""
        a, gone = repo / "packages/a/src/a.ts", repo / "packages/gone/src/g.ts"
        cfg = CallGraph(language="typescript")
        cfg.add_node(Node("a.f", NodeType.FUNCTION, str(a), 1, 2))
        cfg.add_node(Node("gone.g", NodeType.FUNCTION, str(gone), 1, 2))
        cfg.add_edge("gone.g", "a.f")
        cached = StaticAnalysisResults()
        cached.add_cfg(Language.TYPESCRIPT, cfg)
        cached.add_source_files(Language.TYPESCRIPT, [str(a), str(gone)])
        return cached

    def test_a_language_with_nothing_changed_keeps_its_cached_graph_less_deleted_files(self, repo: Path):
        """The deleted package's engine has no files left to run, and no other engine had a change."""
        analyzer, engines = self._analyzer(repo, {repo / "packages/gone/src/g.ts", repo / "tool.py"})
        gone = EngineConfig(_adapter(), repo, [], deferred=True, files_root=repo / "packages/gone")
        analyzer._engine_configs = [engines["a"], engines["b"], gone]
        spawn = MagicMock()
        with (
            patch.dict("os.environ", {}, clear=True),
            patch.object(analyzer, "_spawn_engine_client", spawn),
            patch("static_analyzer.track_lsp_result"),
        ):
            results = analyzer._update_cached_results(self._cached(repo), cached_sha="baseline")
        spawn.assert_not_called()
        assert set(results.get_cfg(Language.TYPESCRIPT).nodes) == {"a.f"}
        assert results.get_source_files(Language.TYPESCRIPT) == [str(repo / "packages/a/src/a.ts")]

    def test_a_changed_eager_engine_without_a_server_keeps_the_cache(self, repo: Path):
        """Its server failed to start, and the deferred engines kept the analyzer going."""
        analyzer, _engines = self._analyzer(repo, {repo / "lib/core.py"})
        python = MagicMock()
        python.language = "Python"
        python.results_language = Language.PYTHON
        python.file_extensions = (".py",)
        analyzer._engine_configs.append(EngineConfig(python, repo, [repo / "lib/core.py"]))
        with pytest.raises(StaticAnalysisFatalError, match="no running Python server"):
            self._run(analyzer, MagicMock())

    def test_every_engine_of_a_language_keeps_its_external_call_sites_for_the_merge(self, repo: Path):
        analyzer, _engines = self._analyzer(repo, {repo / "packages/a/src/a.ts", repo / "packages/b/src/b.ts"})
        updates = iter([{"external_call_sites": ["from a"]}, {"external_call_sites": ["from b"]}])
        with (
            patch.dict("os.environ", {}, clear=True),
            patch.object(analyzer, "_spawn_engine_client", MagicMock(return_value=MagicMock())),
            patch("static_analyzer.update_cfg_for_changed_files", side_effect=lambda *_a: next(updates)),
            patch.object(analyzer, "_extract_language_dict", return_value={}),
            patch.object(analyzer, "_absorb_and_link") as absorb,
            patch.object(analyzer, "_collect_diagnostics_for"),
            patch("static_analyzer.track_lsp_result"),
        ):
            analyzer._update_cached_results(MagicMock(), cached_sha="baseline")
        [(_adapter_used, analysis)] = absorb.call_args.args[1]
        assert analysis["external_call_sites"] == ["from a", "from b"]
