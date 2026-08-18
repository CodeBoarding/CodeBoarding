"""Tests for the engine-concurrency bound.

The bound exists because a monorepo emits one engine per sub-project and
starting them all at once costs peak memory for work that then runs one engine
at a time.
"""

import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from static_analyzer.constants import Language
from static_analyzer import (
    MAX_CONCURRENT_ENGINES_ENV_VAR,
    EngineConfig,
    StaticAnalysisFatalError,
    StaticAnalyzer,
    max_concurrent_engines,
    recommended_engine_concurrency,
)
from static_analyzer.engine.lsp_constants import MIN_ENGINE_MEMORY_BUDGET
from static_analyzer.engine.lsp_recycler import (
    default_memory_budget,
    per_engine_memory_budget,
)


class TestMaxConcurrentEngines:
    def test_unset_stays_unbounded(self):
        with patch.dict("os.environ", {}, clear=True):
            assert max_concurrent_engines() == 0

    def test_explicit_value_is_honoured(self):
        with patch.dict("os.environ", {MAX_CONCURRENT_ENGINES_ENV_VAR: "3"}):
            assert max_concurrent_engines() == 3

    def test_zero_disables_the_bound(self):
        with patch.dict("os.environ", {MAX_CONCURRENT_ENGINES_ENV_VAR: "0"}):
            assert max_concurrent_engines() == 0

    def test_negative_is_ignored(self):
        with patch.dict("os.environ", {MAX_CONCURRENT_ENGINES_ENV_VAR: "-4"}):
            assert max_concurrent_engines() == 0

    def test_non_integer_is_treated_as_unset(self):
        with patch.dict("os.environ", {MAX_CONCURRENT_ENGINES_ENV_VAR: "lots"}):
            assert max_concurrent_engines() == 0


class TestRecommendedEngineConcurrency:
    """The bound is the smallest of work, CPU and memory. Each test pins one."""

    def test_cpu_binds_on_a_modest_host(self):
        # 12 cores / 3 = 4, which is where the abp sweep actually peaked.
        with patch("static_analyzer.default_memory_budget", return_value=64 * 1024**3):
            with patch("os.cpu_count", return_value=12):
                assert recommended_engine_concurrency(28) == 4

    def test_memory_binds_on_a_large_host(self):
        # 128 cores would allow 42 on CPU alone; a 12GB budget supports 6.
        with patch("static_analyzer.default_memory_budget", return_value=12 * 1024**3):
            with patch("os.cpu_count", return_value=128):
                assert recommended_engine_concurrency(28) == 6

    def test_available_work_binds_for_an_ordinary_repo(self):
        with patch("static_analyzer.default_memory_budget", return_value=64 * 1024**3):
            with patch("os.cpu_count", return_value=128):
                assert recommended_engine_concurrency(2) == 2

    def test_never_drops_below_one(self):
        with patch("static_analyzer.default_memory_budget", return_value=1024**3):
            with patch("os.cpu_count", return_value=2):
                assert recommended_engine_concurrency(28) == 1

    def test_unknown_core_count_still_yields_a_usable_bound(self):
        with patch("static_analyzer.default_memory_budget", return_value=64 * 1024**3):
            with patch("os.cpu_count", return_value=None):
                assert recommended_engine_concurrency(28) == 1


class TestLiveClientsGuard:
    """Under the bound nothing is resident, so callers that need a client must be refused."""

    def _analyzer(self, tmp_path: Path, clients: list) -> StaticAnalyzer:
        analyzer = StaticAnalyzer.__new__(StaticAnalyzer)
        analyzer.repository_path = tmp_path
        adapter = MagicMock()
        adapter.file_extensions = [".cs"]
        analyzer._engine_configs = [EngineConfig(adapter, tmp_path, source_files=[tmp_path / "A.cs"])]
        analyzer._engine_clients = clients
        return analyzer

    def test_refuses_a_warm_start_with_no_resident_client(self, tmp_path: Path):
        analyzer = self._analyzer(tmp_path, [])
        with pytest.raises(StaticAnalysisFatalError, match=MAX_CONCURRENT_ENGINES_ENV_VAR):
            analyzer._live_clients("warm-start")

    def test_allows_the_ordinary_path(self, tmp_path: Path):
        clients = [(MagicMock(), MagicMock())]
        assert self._analyzer(tmp_path, clients)._live_clients("warm-start") == clients

    def test_stays_quiet_when_there_is_genuinely_nothing_to_analyze(self, tmp_path: Path):
        analyzer = self._analyzer(tmp_path, [])
        analyzer._engine_configs = [EngineConfig(MagicMock(), tmp_path)]
        assert analyzer._live_clients("warm-start") == []


class TestPrepareProjectOnce:
    def _analyzer(self, tmp_path: Path) -> StaticAnalyzer:
        analyzer = StaticAnalyzer.__new__(StaticAnalyzer)
        analyzer._prepared_projects = {}
        analyzer._prepared_lock = __import__("threading").Lock()
        return analyzer

    def test_prepares_once(self, tmp_path: Path):
        analyzer = self._analyzer(tmp_path)
        adapter = MagicMock()
        adapter.language = "CSharp"
        config = EngineConfig(adapter, tmp_path)

        analyzer._prepare_project_once(config)
        analyzer._prepare_project_once(config)

        assert adapter.prepare_project.call_count == 1

    def test_a_failure_is_re_raised_rather_than_retried(self, tmp_path: Path):
        analyzer = self._analyzer(tmp_path)
        adapter = MagicMock()
        adapter.language = "CSharp"
        adapter.prepare_project.side_effect = RuntimeError("no SDK")
        config = EngineConfig(adapter, tmp_path)

        with pytest.raises(RuntimeError, match="no SDK"):
            analyzer._prepare_project_once(config)
        with pytest.raises(RuntimeError, match="no SDK"):
            analyzer._prepare_project_once(config)

        assert adapter.prepare_project.call_count == 1

    def test_one_language_does_not_suppress_another_at_the_same_root(self, tmp_path: Path):
        analyzer = self._analyzer(tmp_path)
        python, csharp = MagicMock(), MagicMock()
        python.language, csharp.language = "Python", "CSharp"

        analyzer._prepare_project_once(EngineConfig(python, tmp_path))
        analyzer._prepare_project_once(EngineConfig(csharp, tmp_path))

        assert python.prepare_project.call_count == 1
        assert csharp.prepare_project.call_count == 1


class TestBoundedFullPass:
    """The bounded pass must keep the eager path's failure behaviour."""

    def _analyzer(self, tmp_path: Path, engines: int) -> StaticAnalyzer:
        analyzer = StaticAnalyzer.__new__(StaticAnalyzer)
        analyzer.repository_path = tmp_path
        analyzer.collected_diagnostics = {}
        analyzer._engine_clients = []
        analyzer._engine_configs = []
        for i in range(engines):
            adapter = MagicMock()
            adapter.language = "CSharp"
            adapter.language_enum = Language.CSHARP
            analyzer._engine_configs.append(
                EngineConfig(adapter, tmp_path / f"p{i}", source_files=[tmp_path / f"p{i}" / "A.cs"])
            )
        analyzer._loc_for_adapter = MagicMock(return_value=0)
        analyzer._absorb_into_results = MagicMock()
        analyzer._collect_diagnostics_for = MagicMock()
        return analyzer

    def test_every_client_failing_to_start_raises(self, tmp_path: Path):
        analyzer = self._analyzer(tmp_path, engines=3)
        analyzer._spawn_engine_client = MagicMock(side_effect=RuntimeError("csharp-ls missing"))
        analyzer._run_full_analysis = MagicMock()

        with patch.dict("os.environ", {MAX_CONCURRENT_ENGINES_ENV_VAR: "2"}):
            with pytest.raises(RuntimeError, match="Failed to start any engine LSP client"):
                analyzer._run_full_lsp_pass()

    def test_one_survivor_is_not_a_total_failure(self, tmp_path: Path):
        analyzer = self._analyzer(tmp_path, engines=3)
        calls = {"n": 0}

        def spawn(_config):
            calls["n"] += 1
            if calls["n"] > 1:
                raise RuntimeError("csharp-ls missing")
            return MagicMock()

        analyzer._spawn_engine_client = spawn
        analyzer._run_full_analysis = MagicMock(return_value={})

        with patch.dict("os.environ", {MAX_CONCURRENT_ENGINES_ENV_VAR: "1"}):
            analyzer._run_full_lsp_pass()

    def test_a_fatal_error_cancels_the_engines_still_queued(self, tmp_path: Path):
        analyzer = self._analyzer(tmp_path, engines=12)
        analyzer._spawn_engine_client = MagicMock(side_effect=lambda _c: MagicMock())
        analyzed: list[Path] = []

        def analyse(config, _client):
            analyzed.append(config.project_path)
            if len(analyzed) == 1:
                raise StaticAnalysisFatalError("0 symbols")
            time.sleep(0.05)
            return {}

        analyzer._run_full_analysis = analyse

        with patch.dict("os.environ", {MAX_CONCURRENT_ENGINES_ENV_VAR: "2"}):
            with pytest.raises(StaticAnalysisFatalError):
                analyzer._run_full_lsp_pass()

        assert len(analyzed) < 12


class TestPerEngineMemoryBudget:
    """The bound must contain peak memory, not multiply it."""

    def test_single_engine_gets_the_whole_allowance(self):
        assert per_engine_memory_budget(1) == default_memory_budget()
        assert per_engine_memory_budget(0) == default_memory_budget()

    def test_concurrent_engines_share_one_allowance(self):
        total = default_memory_budget()
        for resident in (2, 3, 4):
            share = per_engine_memory_budget(resident)
            assert share * resident <= max(total, MIN_ENGINE_MEMORY_BUDGET * resident)
            assert share <= total

    def test_share_never_falls_below_a_usable_floor(self):
        # Below the floor a server recycles faster than it can index, so the
        # split stops rather than shrinking without limit.
        assert per_engine_memory_budget(1000) == MIN_ENGINE_MEMORY_BUDGET
