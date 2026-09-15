"""Tests for the per-request LSP timeout override."""

from unittest.mock import patch

import pytest

from static_analyzer import LSP_REQUEST_TIMEOUT_ENV_VAR, lsp_request_timeout_override


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
