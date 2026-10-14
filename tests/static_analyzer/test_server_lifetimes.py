"""How many language servers run at once in a one-shot run and in a resident (editor) analyzer."""

import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from static_analyzer import MAX_CONCURRENT_ENGINES_ENV_VAR, EngineConfig, StaticAnalyzer, get_static_analysis
from static_analyzer.cfg import CallGraph
from static_analyzer.config import Language
from static_analyzer.exceptions import StaticAnalysisFatalError


@pytest.mark.parametrize(
    "resident_servers, env, slots",
    [(True, {}, 0), (False, {}, 1), (True, {MAX_CONCURRENT_ENGINES_ENV_VAR: "3"}, 3)],
)
def test_server_slots(tmp_path: Path, resident_servers: bool, env: dict, slots: int):
    with patch.dict("os.environ", env, clear=True), patch("static_analyzer.ProjectScanner"):
        assert StaticAnalyzer(tmp_path, resident_servers=resident_servers)._server_slots == slots


def test_get_static_analysis_runs_one_shot(tmp_path: Path):
    with patch("static_analyzer.StaticAnalyzer") as analyzer_cls:
        get_static_analysis(tmp_path, cache_dir=tmp_path)

    assert analyzer_cls.call_args.kwargs["resident_servers"] is False


def test_a_one_shot_full_pass_runs_one_server_at_a_time_on_the_calling_thread(tmp_path: Path):
    analyzer, servers = _analyzer(tmp_path, resident_servers=False)
    threads: list[threading.Thread] = []

    def analyse(config: EngineConfig, _client: MagicMock) -> dict:
        threads.append(threading.current_thread())
        return _analysis(config)

    with _patched(analyzer, servers, analyse):
        analyzer.start_clients()
        assert servers.running == []
        analyzer._run_full_lsp_pass()

    assert servers.peak == 1
    assert servers.started == analyzer._engine_configs
    assert servers.running == []
    assert threads == [threading.current_thread()] * 3


def test_a_resident_analyzer_keeps_one_server_per_config_up(tmp_path: Path):
    analyzer, servers = _analyzer(tmp_path, resident_servers=True)

    with _patched(analyzer, servers, lambda config, _client: _analysis(config)):
        analyzer.start_clients()
        analyzer._run_full_lsp_pass()

    assert servers.running == analyzer._engine_configs


def test_a_one_shot_warm_start_starts_each_server_for_its_own_step(tmp_path: Path):
    analyzer, servers = _analyzer(tmp_path, resident_servers=False)
    analyzer.changed_files = {tmp_path / "app.py"}

    with (
        _patched(analyzer, servers, lambda config, _client: _analysis(config)),
        patch("static_analyzer.update_cfg_for_changed_files", side_effect=lambda cached, *_args: cached),
        patch.object(analyzer, "_extract_language_dict", return_value={}),
    ):
        analyzer.start_clients()
        analyzer._update_cached_results(MagicMock(), "baseline")

    assert servers.peak == 1
    assert servers.started == analyzer._engine_configs
    assert servers.running == []


def test_a_one_shot_warm_start_stops_the_server_of_a_step_that_fails(tmp_path: Path):
    analyzer, servers = _analyzer(tmp_path, resident_servers=False)
    analyzer.changed_files = {tmp_path / "app.py"}

    with (
        _patched(analyzer, servers, lambda config, _client: _analysis(config)),
        patch("static_analyzer.update_cfg_for_changed_files", side_effect=RuntimeError("pyright died")),
        patch.object(analyzer, "_extract_language_dict", return_value={}),
    ):
        analyzer.start_clients()
        with pytest.raises(RuntimeError, match="pyright died"):
            analyzer._update_cached_results(MagicMock(), "baseline")

    assert servers.started == analyzer._engine_configs[:1]
    assert servers.running == []


@pytest.mark.parametrize("error, skipped", [(RuntimeError("no dotnet"), True), (StaticAnalysisFatalError("x"), False)])
def test_a_one_shot_warm_start_skips_a_language_whose_server_cannot_start(
    tmp_path: Path, error: Exception, skipped: bool
):
    analyzer, servers = _analyzer(tmp_path, resident_servers=False)
    analyzer.changed_files = {tmp_path / "app.py"}

    def spawn(config: EngineConfig) -> MagicMock:
        if config.adapter.language_enum is Language.TYPESCRIPT:
            raise error
        return servers.spawn(config)

    with (
        _patched(analyzer, servers, lambda config, _client: _analysis(config)),
        patch.object(analyzer, "_spawn_engine_client", side_effect=spawn),
        patch("static_analyzer.update_cfg_for_changed_files", side_effect=lambda cached, *_args: cached),
        patch.object(analyzer, "_extract_language_dict", return_value={}),
    ):
        analyzer.start_clients()
        if skipped:
            analyzer._update_cached_results(MagicMock(), "baseline")
        else:
            with pytest.raises(StaticAnalysisFatalError):
                analyzer._update_cached_results(MagicMock(), "baseline")

    expected = [analyzer._engine_configs[0], analyzer._engine_configs[2]] if skipped else analyzer._engine_configs[:1]
    assert servers.started == expected
    assert servers.running == []


def test_a_warm_start_where_no_server_starts_raises_instead_of_returning_an_empty_result(tmp_path: Path):
    analyzer, _servers = _analyzer(tmp_path, resident_servers=False)
    analyzer.changed_files = {tmp_path / "app.py"}

    with (
        patch.object(analyzer, "_spawn_engine_client", side_effect=RuntimeError("no node")),
        patch("static_analyzer.track_lsp_result"),
    ):
        analyzer.start_clients()
        results = analyzer._update_cached_results(MagicMock(), "baseline")

    with pytest.raises(RuntimeError, match="No language could be analysed"):
        analyzer._validate_analysis_results(results)


def test_a_warm_start_full_re_analysis_that_fails_raises_instead_of_leaving_the_language_out(tmp_path: Path):
    analyzer, servers = _analyzer(tmp_path, resident_servers=False)

    def analyse(config: EngineConfig, _client: MagicMock) -> dict:
        if config.adapter.language_enum is Language.TYPESCRIPT:
            raise RuntimeError("tsserver crashed")
        return _analysis(config)

    with (
        _patched(analyzer, servers, analyse),
        patch.object(analyzer, "_changed_files_for_language", return_value=None),
    ):
        analyzer.start_clients()
        with pytest.raises(RuntimeError, match="tsserver crashed"):
            analyzer._update_cached_results(MagicMock(), "baseline")

    assert servers.running == []


class _Servers:
    """Every server a test starts, and the most that ran at once."""

    def __init__(self) -> None:
        self.running: list[EngineConfig] = []
        self.started: list[EngineConfig] = []
        self.peak = 0

    def spawn(self, config: EngineConfig) -> MagicMock:
        server = MagicMock(name=str(config.project_path))
        server.shutdown.side_effect = lambda: self.running.remove(config)
        self.running.append(config)
        self.started.append(config)
        self.peak = max(self.peak, len(self.running))
        return server


def _analyzer(tmp_path: Path, resident_servers: bool) -> tuple[StaticAnalyzer, _Servers]:
    with patch.dict("os.environ", {}, clear=True), patch("static_analyzer.ProjectScanner"):
        analyzer = StaticAnalyzer(tmp_path, resident_servers=resident_servers)
    analyzer._engine_configs = [
        EngineConfig(_adapter(language), tmp_path, [tmp_path / name])
        for language, name in [(Language.PYTHON, "app.py"), (Language.TYPESCRIPT, "a.ts"), (Language.GO, "main.go")]
    ]
    analyzer._loc_for_adapter = MagicMock(return_value=0)  # type: ignore[method-assign]
    analyzer._collect_diagnostics_for = MagicMock()  # type: ignore[method-assign]
    return analyzer, _Servers()


@contextmanager
def _patched(analyzer: StaticAnalyzer, servers: _Servers, analyse) -> Iterator[None]:
    with (
        patch.object(analyzer, "_spawn_engine_client", side_effect=servers.spawn),
        patch.object(analyzer, "_run_full_analysis", side_effect=analyse),
        patch.object(analyzer, "_absorb_and_link"),
        patch("static_analyzer.track_lsp_result"),
    ):
        yield


def _adapter(language: Language) -> MagicMock:
    adapter = MagicMock()
    adapter.language = language.value
    adapter.language_enum = language
    adapter.results_language = language
    adapter.rebuilds_for.return_value = False
    return adapter


def _analysis(config: EngineConfig) -> dict:
    return {"call_graph": CallGraph(), "source_files": config.source_files}
