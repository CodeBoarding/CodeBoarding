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
from static_analyzer.config import Language
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
        # Real adapters default results_language to language_enum; a MagicMock would not.
        adapter.results_language = language_enum
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


class TestStartClientsSkipsEmptyLanguages:
    """A detected language with no files of its own must not cost an LSP process.

    The scanner flags a language from a handful of stray files, and starting its
    server (tsserver plus its node workers, ~350MB) to then analyze nothing is
    pure overhead held for the whole run.
    """

    def test_language_without_source_files_starts_no_client(self, analyzer: StaticAnalyzer, tmp_path: Path) -> None:
        py_adapter = _make_adapter("Python")
        py_adapter.discover_source_files.return_value = [tmp_path / "a.py"]  # type: ignore[attr-defined]
        js_adapter = _make_adapter("JavaScript")
        js_adapter.discover_source_files.return_value = []  # type: ignore[attr-defined]
        analyzer._engine_configs = [EngineConfig(py_adapter, tmp_path), EngineConfig(js_adapter, tmp_path)]

        with patch("static_analyzer.LSPClient", side_effect=[MagicMock(name="PythonClient")]) as client_cls:
            analyzer.start_clients()

        assert client_cls.call_count == 1
        assert [c.adapter.language for c, _ in analyzer._engine_clients] == ["Python"]

    def test_discovered_files_are_reused_not_rewalked(self, analyzer: StaticAnalyzer, tmp_path: Path) -> None:
        adapter = _make_adapter("Python")
        discovered = [tmp_path / "a.py"]
        adapter.discover_source_files.return_value = discovered  # type: ignore[attr-defined]
        config = EngineConfig(adapter, tmp_path)
        analyzer._engine_configs = [config]

        with patch("static_analyzer.LSPClient", side_effect=[MagicMock()]):
            analyzer.start_clients()

        assert config.source_files == discovered

    def test_no_language_with_files_is_not_a_failure(self, analyzer: StaticAnalyzer, tmp_path: Path) -> None:
        adapter = _make_adapter("JavaScript")
        adapter.discover_source_files.return_value = []  # type: ignore[attr-defined]
        analyzer._engine_configs = [EngineConfig(adapter, tmp_path)]

        with patch("static_analyzer.LSPClient") as client_cls:
            analyzer.start_clients()  # must not raise: nothing to analyze != failure

        client_cls.assert_not_called()
        assert analyzer._clients_started is True
        assert analyzer._engine_clients == []


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
        analyzer._engine_configs = [EngineConfig(cs_adapter, tmp_path)]

        results = StaticAnalysisResults()
        results.add_source_files(Language.CSHARP, [str(tmp_path / "Program.cs")])

        with pytest.raises(StaticAnalysisFatalError, match="0 symbols"):
            analyzer._validate_analysis_results(results)

    def test_validate_ignores_empty_non_opted_language(self, analyzer: StaticAnalyzer, tmp_path: Path) -> None:
        py_adapter = _make_adapter("Python", language_enum=Language.PYTHON, fail_on_empty_symbols=False)
        analyzer._engine_configs = [EngineConfig(py_adapter, tmp_path)]

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
