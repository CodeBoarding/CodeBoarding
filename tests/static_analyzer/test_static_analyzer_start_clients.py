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

from static_analyzer import StaticAnalyzer
from static_analyzer.engine.language_adapter import LanguageAdapter


def _make_adapter(language: str, *, wait_for_workspace_ready: bool = False) -> LanguageAdapter:
    adapter = MagicMock(name=f"{language}Adapter")
    adapter.language = language
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
            (py_adapter, tmp_path),
            (cs_adapter, tmp_path),
            (ts_adapter, tmp_path),
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
        assert [a.language for a, _, _ in analyzer._engine_clients] == ["Python", "TypeScript"]
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
        analyzer._engine_configs = [(py_adapter, tmp_path), (cs_adapter, tmp_path)]

        bad_py = MagicMock()
        bad_py.start.side_effect = RuntimeError("pyright missing")
        bad_cs = MagicMock()
        bad_cs.start.side_effect = TimeoutError("omnisharp timed out")

        with patch("static_analyzer.LSPClient", side_effect=[bad_py, bad_cs]):
            with pytest.raises(RuntimeError, match=r"attempted:.*Python.*CSharp"):
                analyzer.start_clients()

        assert analyzer._clients_started is False
        assert analyzer._engine_clients == []

    def test_all_success_records_no_failures(self, analyzer: StaticAnalyzer, tmp_path: Path) -> None:
        py_adapter = _make_adapter("Python")
        ts_adapter = _make_adapter("TypeScript")
        analyzer._engine_configs = [(py_adapter, tmp_path), (ts_adapter, tmp_path)]

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
        analyzer._engine_configs = [(rust_adapter, tmp_path)]

        client = MagicMock(name="RustClient")
        with patch("static_analyzer.LSPClient", return_value=client):
            analyzer.start_clients()

        client.wait_for_server_ready.assert_called_once()

    def test_adapter_not_opting_in_skips_wait(self, analyzer: StaticAnalyzer, tmp_path: Path) -> None:
        py_adapter = _make_adapter("Python", wait_for_workspace_ready=False)
        analyzer._engine_configs = [(py_adapter, tmp_path)]

        client = MagicMock(name="PythonClient")
        with patch("static_analyzer.LSPClient", return_value=client):
            analyzer.start_clients()

        client.wait_for_server_ready.assert_not_called()

    def test_mixed_adapters_only_waits_on_opting_in_clients(self, analyzer: StaticAnalyzer, tmp_path: Path) -> None:
        rust_adapter = _make_adapter("Rust", wait_for_workspace_ready=True)
        py_adapter = _make_adapter("Python", wait_for_workspace_ready=False)
        analyzer._engine_configs = [(py_adapter, tmp_path), (rust_adapter, tmp_path)]

        py_client = MagicMock(name="PythonClient")
        rust_client = MagicMock(name="RustClient")
        with patch("static_analyzer.LSPClient", side_effect=[py_client, rust_client]):
            analyzer.start_clients()

        py_client.wait_for_server_ready.assert_not_called()
        rust_client.wait_for_server_ready.assert_called_once()
