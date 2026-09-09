"""Tests for the per-request LSP timeout override.

The override exists because an adapter's fixed ceiling is calibrated on a
mid-sized workspace, and a larger one can exceed it with the server still
healthy — see the C# solution in issue #529.
"""

from unittest.mock import MagicMock, patch

from static_analyzer import LSP_REQUEST_TIMEOUT_ENV_VAR, lsp_request_timeout


def _adapter() -> MagicMock:
    adapter = MagicMock()
    adapter.language = "CSharp"
    adapter.get_lsp_default_timeout.return_value = 120
    return adapter


class TestLspRequestTimeout:
    def test_unset_keeps_the_adapter_default(self):
        with patch.dict("os.environ", {}, clear=True):
            assert lsp_request_timeout(_adapter()) == 120

    def test_explicit_value_is_honoured(self):
        with patch.dict("os.environ", {LSP_REQUEST_TIMEOUT_ENV_VAR: "900"}):
            assert lsp_request_timeout(_adapter()) == 900

    def test_zero_is_ignored(self):
        with patch.dict("os.environ", {LSP_REQUEST_TIMEOUT_ENV_VAR: "0"}):
            assert lsp_request_timeout(_adapter()) == 120

    def test_negative_is_ignored(self):
        with patch.dict("os.environ", {LSP_REQUEST_TIMEOUT_ENV_VAR: "-30"}):
            assert lsp_request_timeout(_adapter()) == 120

    def test_non_integer_is_ignored(self):
        with patch.dict("os.environ", {LSP_REQUEST_TIMEOUT_ENV_VAR: "ages"}):
            assert lsp_request_timeout(_adapter()) == 120

    def test_whitespace_is_treated_as_unset(self):
        with patch.dict("os.environ", {LSP_REQUEST_TIMEOUT_ENV_VAR: "  "}):
            assert lsp_request_timeout(_adapter()) == 120
