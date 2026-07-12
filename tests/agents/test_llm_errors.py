"""Tests for provider-agnostic LLM auth-error detection and typing."""

import os
from unittest.mock import patch

from agents.llm_config import current_provider_key_context
from agents.llm_errors import LLMAuthError, detect_auth_error


class _FakeStatusError(Exception):
    """Mimics openai/anthropic APIStatusError: carries a status_code."""

    def __init__(self, message: str, status_code: int):
        super().__init__(message)
        self.status_code = status_code


class _FakeGoogleError(Exception):
    """Mimics google GoogleAPICallError: carries a numeric code."""

    def __init__(self, message: str, code: int):
        super().__init__(message)
        self.code = code


class TestDetectAuthError:
    def test_openai_style_401_detected(self):
        exc = _FakeStatusError("Error code: 401 - invalid api key", status_code=401)
        result = detect_auth_error(exc, provider="openai", key_tail="a8dd")
        assert isinstance(result, LLMAuthError)
        assert result.provider == "openai"
        assert result.key_tail == "a8dd"

    def test_403_detected(self):
        exc = _FakeStatusError("permission denied", status_code=403)
        assert detect_auth_error(exc, provider="aws", key_tail="xxxx") is not None

    def test_google_unauthenticated_code_detected(self):
        exc = _FakeGoogleError("API key not valid", code=401)
        assert detect_auth_error(exc, provider="google", key_tail="1234") is not None

    def test_class_name_match_without_status_code(self):
        # A bare class named AuthenticationError with no status_code still counts.
        AuthenticationError = type("AuthenticationError", (Exception,), {})
        exc = AuthenticationError("nope")
        assert detect_auth_error(exc, provider="anthropic", key_tail="9999") is not None

    def test_message_pattern_match(self):
        # langchain/botocore sometimes re-wrap so only the string survives.
        exc = RuntimeError("Error code: 401 - {'message': 'invalid x-api-key'}")
        assert detect_auth_error(exc, provider="anthropic", key_tail="0000") is not None

    def test_github_oidc_message_detected(self):
        exc = RuntimeError("Error code: 401 - Invalid GitHub OIDC token.")
        assert detect_auth_error(exc, provider="openrouter", key_tail="tok0") is not None

    def test_non_auth_error_returns_none(self):
        assert detect_auth_error(TimeoutError("timed out"), provider="openai", key_tail="a8dd") is None
        assert detect_auth_error(ValueError("bad json"), provider="openai", key_tail="a8dd") is None
        assert (
            detect_auth_error(_FakeStatusError("not found", status_code=404), provider="openai", key_tail="x") is None
        )

    def test_existing_auth_error_passes_through_unchanged(self):
        original = LLMAuthError("boom", provider="openai", key_tail="a8dd", telemetry_properties={"error_type": "auth"})
        assert detect_auth_error(original, provider="ignored", key_tail="ignored") is original

    def test_telemetry_properties_are_attached(self):
        exc = _FakeStatusError("Error code: 401 - your key is invalid", status_code=401)
        result = detect_auth_error(exc, provider="openai", key_tail="a8dd")
        assert result is not None
        props = result.telemetry_properties
        assert props["error_type"] == "auth"
        assert props["error_provider"] == "openai"
        assert props["error_status_code"] == 401
        assert props["error_key_tail"] == "a8dd"
        assert "invalid" in props["error_message"]

    def test_message_names_provider_and_key_tail(self):
        exc = _FakeStatusError("401", status_code=401)
        result = detect_auth_error(exc, provider="anthropic", key_tail="a8dd")
        assert result is not None
        assert "anthropic" in str(result)
        assert "a8dd" in str(result)

    def test_message_truncated_to_bound(self):
        exc = _FakeStatusError("x" * 5000, status_code=401)
        result = detect_auth_error(exc, provider="openai", key_tail="a8dd")
        assert result is not None
        assert len(result.telemetry_properties["error_message"]) <= 500


class TestCurrentProviderKeyContext:
    def test_masks_all_but_last_four(self):
        with patch.dict(os.environ, {"OPENAI_API_KEY": "sk-secret-abcd"}, clear=True):
            name, tail = current_provider_key_context()
        assert name == "openai"
        assert tail == "abcd"

    def test_no_provider_returns_unknown(self):
        with patch.dict(os.environ, {}, clear=True):
            assert current_provider_key_context() == ("unknown", "unknown")

    def test_keyless_endpoint_returns_unknown_tail(self):
        # Selected via base URL, no real key set.
        with patch.dict(os.environ, {"OPENAI_BASE_URL": "http://127.0.0.1:8000/v1"}, clear=True):
            name, tail = current_provider_key_context()
        assert name == "openai"
        assert tail == "unknown"
