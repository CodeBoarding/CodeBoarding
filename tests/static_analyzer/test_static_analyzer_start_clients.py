"""Tests for ``StaticAnalyzer.start_clients`` graceful-degradation behavior.

Covers the failure-mode contract introduced for issue #280: a single
language's LSP client failing to start must not tear down the other
clients, and a total failure must raise a ``RuntimeError`` listing all
attempted languages.
"""

from pathlib import Path
from typing import cast
from unittest.mock import MagicMock, patch

import pytest

from static_analyzer import EngineConfig, StaticAnalysisFatalError, StaticAnalyzer
from static_analyzer.analysis_result import StaticAnalysisResults
from static_analyzer.constants import Language
from static_analyzer.engine.language_adapter import LanguageAdapter


def _make_adapter(
    language: str,
    *,
    wait_for_workspace_ready: bool = False,
    language_enum: Language | None = None,
    fail_on_empty_symbols: bool = False,
) -> LanguageAdapter:
    adapter = MagicMock(name=f"{language}Adapter")
    adapter.language = language
    if language_enum is not None:
        adapter.language_enum = language_enum
    adapter.fail_on_empty_symbols = fail_on_empty_symbols
    adapter.get_lsp_command.return_value = [f"{language.lower()}-lsp"]
    adapter.get_lsp_init_options.return_value = {}
    adapter.get_lsp_env.return_value = {}
    adapter.get_workspace_settings.return_value = {}
    adapter.wait_for_workspace_ready = wait_for_workspace_ready
    return cast(LanguageAdapter, adapter)


@pytest.fixture
def analyzer(tmp_path: Path) -> StaticAnalyzer:
    # Bypass ProjectScanner / config discovery — we inject _engine_configs directly.
    with patch("static_analyzer.ProjectScanner") as scanner_cls:
        scanner_cls.return_value.scan.return_value = []
        sa = StaticAnalyzer(tmp_path)
    return sa


class TestStartClientsGracefulDegradation:
    def test_partial_failure_skips_failing_language_and_continues(
        self, analyzer: StaticAnalyzer, tmp_path: Path
    ) -> None:
        py_adapter = _make_adapter("Python")
        cs_adapter = _make_adapter("CSharp")
        ts_adapter = _make_adapter("TypeScript")
        analyzer._engine_configs = [
            EngineConfig(py_adapter, tmp_path),
            EngineConfig(cs_adapter, tmp_path),
            EngineConfig(ts_adapter, tmp_path),
        ]

        good_client_py = MagicMock(name="PythonClient")
        bad_client_cs = MagicMock(name="CSharpClient")
        bad_client_cs.start.side_effect = TimeoutError("OmniSharp timed out")
        good_client_ts = MagicMock(name="TypeScriptClient")

        with patch(
            "static_analyzer.LSPClient",
            side_effect=[good_client_py, bad_client_cs, good_client_ts],
        ):
            analyzer.start_clients()

        assert analyzer._clients_started is True
        assert [c.adapter.language for c, _ in analyzer._engine_clients] == ["Python", "TypeScript"]
        # Healthy clients must NOT be shut down because a sibling failed.
        good_client_py.shutdown.assert_not_called()
        good_client_ts.shutdown.assert_not_called()
        # Failing client gets a best-effort shutdown for partial-state cleanup.
        bad_client_cs.shutdown.assert_called_once()

    def test_total_failure_raises_runtime_error_listing_attempted_languages(
        self, analyzer: StaticAnalyzer, tmp_path: Path
    ) -> None:
        py_adapter = _make_adapter("Python")
        cs_adapter = _make_adapter("CSharp")
        analyzer._engine_configs = [EngineConfig(py_adapter, tmp_path), EngineConfig(cs_adapter, tmp_path)]

        bad_py = MagicMock()
        bad_py.start.side_effect = RuntimeError("pyright missing")
        bad_cs = MagicMock()
        bad_cs.start.side_effect = TimeoutError("omnisharp timed out")

        with patch("static_analyzer.LSPClient", side_effect=[bad_py, bad_cs]):
            with pytest.raises(RuntimeError, match=r"attempted:.*Python.*CSharp.*pyright missing") as exc:
                analyzer.start_clients()

        assert "omnisharp timed out" in str(exc.value)
        assert analyzer._clients_started is False
        assert analyzer._engine_clients == []

    def test_validate_rejects_empty_symbol_csharp_result(self, analyzer: StaticAnalyzer, tmp_path: Path) -> None:
        cs_adapter = _make_adapter("CSharp", language_enum=Language.CSHARP, fail_on_empty_symbols=True)
        analyzer._engine_clients = [(EngineConfig(cs_adapter, tmp_path), MagicMock())]

        results = StaticAnalysisResults()
        results.add_source_files(Language.CSHARP, [str(tmp_path / "Program.cs")])

        with pytest.raises(StaticAnalysisFatalError, match="0 symbols"):
            analyzer._validate_analysis_results(results)

    def test_validate_ignores_empty_non_opted_language(self, analyzer: StaticAnalyzer, tmp_path: Path) -> None:
        py_adapter = _make_adapter("Python", language_enum=Language.PYTHON, fail_on_empty_symbols=False)
        analyzer._engine_clients = [(EngineConfig(py_adapter, tmp_path), MagicMock())]

        results = StaticAnalysisResults()
        results.add_source_files(Language.PYTHON, [str(tmp_path / "app.py")])

        analyzer._validate_analysis_results(results)

    def test_all_success_records_no_failures(self, analyzer: StaticAnalyzer, tmp_path: Path) -> None:
        py_adapter = _make_adapter("Python")
        ts_adapter = _make_adapter("TypeScript")
        analyzer._engine_configs = [EngineConfig(py_adapter, tmp_path), EngineConfig(ts_adapter, tmp_path)]

        with patch("static_analyzer.LSPClient", side_effect=[MagicMock(), MagicMock()]):
            analyzer.start_clients()

        assert analyzer._clients_started is True
        assert len(analyzer._engine_clients) == 2


class TestStartClientsWorkspaceReadyDispatch:
    """``start_clients`` calls ``wait_for_server_ready`` exactly when the
    adapter opts in via ``wait_for_workspace_ready``.
    """

    def test_adapter_opting_in_triggers_wait(self, analyzer: StaticAnalyzer, tmp_path: Path) -> None:
        rust_adapter = _make_adapter("Rust", wait_for_workspace_ready=True)
        analyzer._engine_configs = [EngineConfig(rust_adapter, tmp_path)]

        client = MagicMock(name="RustClient")
        with patch("static_analyzer.LSPClient", return_value=client):
            analyzer.start_clients()

        client.wait_for_server_ready.assert_called_once()

    def test_adapter_not_opting_in_skips_wait(self, analyzer: StaticAnalyzer, tmp_path: Path) -> None:
        py_adapter = _make_adapter("Python", wait_for_workspace_ready=False)
        analyzer._engine_configs = [EngineConfig(py_adapter, tmp_path)]

        client = MagicMock(name="PythonClient")
        with patch("static_analyzer.LSPClient", return_value=client):
            analyzer.start_clients()

        client.wait_for_server_ready.assert_not_called()

    def test_mixed_adapters_only_waits_on_opting_in_clients(self, analyzer: StaticAnalyzer, tmp_path: Path) -> None:
        rust_adapter = _make_adapter("Rust", wait_for_workspace_ready=True)
        py_adapter = _make_adapter("Python", wait_for_workspace_ready=False)
        analyzer._engine_configs = [EngineConfig(py_adapter, tmp_path), EngineConfig(rust_adapter, tmp_path)]

        py_client = MagicMock(name="PythonClient")
        rust_client = MagicMock(name="RustClient")
        with patch("static_analyzer.LSPClient", side_effect=[py_client, rust_client]):
            analyzer.start_clients()

        py_client.wait_for_server_ready.assert_not_called()
        rust_client.wait_for_server_ready.assert_called_once()


class TestFlushCacheRespectsCacheDir:
    """``flush_cache`` writes to ``_pending_cache_dir`` (set by ``analyze``)
    when supplied, otherwise to the default ``get_artifact_dir(repository_path)``.
    """

    def test_flush_uses_pending_cache_dir_when_set(self, analyzer: StaticAnalyzer, tmp_path: Path) -> None:
        from static_analyzer.analysis_result import StaticAnalysisResults

        custom_cache = tmp_path / "branches" / "main"
        analyzer._cached_results = StaticAnalysisResults()
        analyzer._pending_cache_dir = custom_cache
        analyzer._pending_source_sha = None
        analyzer._results_need_saving = True  # stand in for a completed analyze()

        analyzer.flush_cache()

        assert (custom_cache / "static_analysis.pkl").exists()
        # Default location must be untouched.
        from utils import get_artifact_dir

        default_pkl = get_artifact_dir(analyzer.repository_path) / "static_analysis.pkl"
        assert not default_pkl.exists()

    def test_flush_uses_default_artifact_dir_when_analyze_was_not_called(self, analyzer: StaticAnalyzer) -> None:
        """Default ``_pending_cache_dir`` is set in ``__init__`` so a flush
        before any ``analyze()`` call still writes to the canonical location.
        """
        from static_analyzer.analysis_result import StaticAnalysisResults
        from utils import get_artifact_dir

        assert analyzer._pending_cache_dir == get_artifact_dir(analyzer.repository_path)

        analyzer._cached_results = StaticAnalysisResults()
        analyzer._pending_source_sha = None
        analyzer._results_need_saving = True  # stand in for a completed analyze()

        analyzer.flush_cache()

        default_pkl = get_artifact_dir(analyzer.repository_path) / "static_analysis.pkl"
        assert default_pkl.exists()


class TestLoadCachedAnalysis:
    def test_read_only_load_is_not_rewritten_on_flush(self, analyzer: StaticAnalyzer, tmp_path: Path) -> None:
        """A read-only ``load_cached_analysis`` must not mark results as produced,
        so a following flush is a no-op and can't strip the artifact's SHA sidecar.
        """
        cached_results = StaticAnalysisResults()

        with (
            patch("static_analyzer.StaticAnalysisCache.get", return_value=cached_results),
            patch("static_analyzer.StaticAnalysisCache.save") as save,
        ):
            assert analyzer.load_cached_analysis(artifact_dir=tmp_path) is cached_results
            analyzer.flush_cache()

        save.assert_not_called()

    def test_analyze_after_read_only_load_persists_with_source_sha(
        self, analyzer: StaticAnalyzer, tmp_path: Path
    ) -> None:
        """A real ``analyze()`` after a read-only load re-arms persistence: the
        flush writes the pkl with the run's ``source_sha``. Guards the
        load-then-analyze ordering the read-only guard must not suppress.
        """
        loaded, produced = StaticAnalysisResults(), StaticAnalysisResults()

        with (
            patch("static_analyzer.StaticAnalysisCache.get", return_value=loaded),
            patch("static_analyzer.StaticAnalysisCache.save") as save,
        ):
            assert analyzer.load_cached_analysis(artifact_dir=tmp_path) is loaded
            with (
                patch.object(analyzer, "_clients_started", True),
                patch.object(analyzer, "_run_full_lsp_pass", return_value=produced),
                patch.object(analyzer, "_validate_analysis_results"),
            ):
                analyzer._cached_results = None  # drop the read-only mem-hit so analyze produces fresh
                analyzer.analyze(cache_dir=tmp_path, skip_cache=True, source_sha="abc123")
            analyzer.flush_cache()

        save.assert_called_once()
        assert save.call_args.kwargs["source_sha"] == "abc123"
