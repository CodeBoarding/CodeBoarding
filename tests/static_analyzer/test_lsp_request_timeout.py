"""Tests for the per-request LSP timeout override.

The override exists because an adapter's fixed ceiling is calibrated on a
mid-sized workspace, and a larger one can exceed it with the server healthy.
"""

from unittest.mock import MagicMock, patch

import pytest

from static_analyzer import (
    LSP_REQUEST_TIMEOUT_ENV_VAR,
    StaticAnalysisFatalError,
    lsp_request_timeout,
)


def _adapter() -> MagicMock:
    adapter = MagicMock()
    adapter.language = "CSharp"
    adapter.get_lsp_default_timeout.return_value = 120
    return adapter


class TestLspRequestTimeout:
    def test_unset_keeps_the_adapter_default(self):
        with patch.dict("os.environ", {}, clear=True):
            assert lsp_request_timeout(_adapter()) == 120

    def test_whitespace_is_treated_as_unset(self):
        with patch.dict("os.environ", {LSP_REQUEST_TIMEOUT_ENV_VAR: "  "}):
            assert lsp_request_timeout(_adapter()) == 120

    def test_explicit_value_is_honoured(self):
        with patch.dict("os.environ", {LSP_REQUEST_TIMEOUT_ENV_VAR: "900"}):
            assert lsp_request_timeout(_adapter()) == 900

    @pytest.mark.parametrize("raw", ["0", "-30"])
    def test_non_positive_raises(self, raw):
        with patch.dict("os.environ", {LSP_REQUEST_TIMEOUT_ENV_VAR: raw}):
            with pytest.raises(StaticAnalysisFatalError, match="positive number of seconds"):
                lsp_request_timeout(_adapter())

    @pytest.mark.parametrize("raw", ["900s", "ages"])
    def test_non_integer_raises(self, raw):
        """Why: a mistyped unit would otherwise restore the ceiling being escaped."""
        with patch.dict("os.environ", {LSP_REQUEST_TIMEOUT_ENV_VAR: raw}):
            with pytest.raises(StaticAnalysisFatalError, match="whole number of seconds"):
                lsp_request_timeout(_adapter())
