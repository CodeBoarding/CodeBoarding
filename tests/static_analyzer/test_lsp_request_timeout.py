"""Tests for the per-request LSP timeout override."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from static_analyzer import (
    LSP_REQUEST_TIMEOUT_ENV_VAR,
    EngineConfig,
    StaticAnalyzer,
    lsp_request_timeout_override,
)


class TestLspRequestTimeoutOverride:
    def test_unset_leaves_the_adapter_in_charge(self):
        with patch.dict("os.environ", {}, clear=True):
            assert lsp_request_timeout_override() is None

    def test_whitespace_is_treated_as_unset(self):
        with patch.dict("os.environ", {LSP_REQUEST_TIMEOUT_ENV_VAR: "  "}):
            assert lsp_request_timeout_override() is None

    def test_explicit_value_is_honoured(self):
        with patch.dict("os.environ", {LSP_REQUEST_TIMEOUT_ENV_VAR: "900"}):
            assert lsp_request_timeout_override() == 900

    @pytest.mark.parametrize("raw", ["0", "-30"])
    def test_non_positive_raises(self, raw):
        with patch.dict("os.environ", {LSP_REQUEST_TIMEOUT_ENV_VAR: raw}):
            with pytest.raises(ValueError, match="positive number of seconds"):
                lsp_request_timeout_override()

    @pytest.mark.parametrize("raw", ["900s", "ages"])
    def test_non_integer_raises(self, raw):
        """Why: a mistyped unit would otherwise restore the ceiling being escaped."""
        with patch.dict("os.environ", {LSP_REQUEST_TIMEOUT_ENV_VAR: raw}):
            with pytest.raises(ValueError, match="whole number of seconds"):
                lsp_request_timeout_override()


def _spawn_with_env(tmp_path: Path, env: dict[str, str]) -> MagicMock:
    """Start one engine under ``env`` and hand back the patched LSPClient class."""
    adapter = MagicMock(name="CSharpAdapter")
    adapter.language = "CSharp"
    adapter.get_lsp_default_timeout.return_value = 120
    adapter.get_lsp_command.return_value = ["csharp-ls"]
    adapter.get_lsp_init_options.return_value = {}
    adapter.get_lsp_env.return_value = {}
    adapter.get_workspace_settings.return_value = {}
    adapter.wait_for_workspace_ready = False
    adapter.discover_source_files.return_value = [tmp_path / "a.cs"]

    with patch.dict("os.environ", env, clear=True):
        with patch("static_analyzer.ProjectScanner") as scanner_cls:
            scanner_cls.return_value.scan.return_value = []
            analyzer = StaticAnalyzer(tmp_path)
        analyzer._engine_configs = [EngineConfig(adapter, tmp_path)]
        with patch("static_analyzer.LSPClient") as client_cls:
            analyzer.start_clients()
    return client_cls


class TestOverrideReachesTheSpawnedClient:
    def test_override_is_passed_as_default_timeout(self, tmp_path):
        client_cls = _spawn_with_env(tmp_path, {LSP_REQUEST_TIMEOUT_ENV_VAR: "900"})

        assert client_cls.call_args.kwargs["default_timeout"] == 900

    def test_without_the_override_the_adapter_decides(self, tmp_path):
        client_cls = _spawn_with_env(tmp_path, {})

        assert client_cls.call_args.kwargs["default_timeout"] == 120
