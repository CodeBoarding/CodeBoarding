"""A warm start starts only the deferred engines whose files changed, each with only its own files.

Why it matters: one tsserver per package means dozens of engines of one language sharing the
repository root, so scoping changes by root would start every server with every change.
"""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from static_analyzer import EngineConfig, StaticAnalysisFatalError, StaticAnalyzer
from static_analyzer.config import Language


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
