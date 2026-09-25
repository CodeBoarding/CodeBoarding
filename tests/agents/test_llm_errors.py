"""Tests for provider-agnostic detection and typing of terminal LLM errors."""

import json
import os
from unittest.mock import patch

import httpx
import openai
import pytest

from agents.llm_config import current_provider_key_context
from agents.llm_errors import (
    LLMAuthError,
    LLMQuotaError,
    LLMTerminalError,
    detect_auth_error,
    detect_quota_error,
    raise_if_terminal_llm_error,
)


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


def test_raise_if_terminal_llm_error_uses_current_provider_context():
    error = _FakeStatusError("invalid api key", status_code=401)
    with patch("agents.llm_errors.current_provider_key_context", return_value=("google", "1234")):
        with pytest.raises(LLMAuthError, match="google") as caught:
            raise_if_terminal_llm_error(error)

    assert caught.value.key_tail == "1234"


def _openai_status_error(status: int, body: dict) -> openai.APIStatusError:
    """Build the error exactly as the openai client does from a proxy's HTTP response."""
    response = httpx.Response(
        status,
        request=httpx.Request("POST", "https://proxy.example/v1/chat/completions"),
        content=json.dumps(body).encode(),
        headers={"content-type": "application/json"},
    )
    return openai.OpenAI(api_key="sk-test")._make_status_error_from_response(response)


class TestDetectQuotaError:
    def test_proxy_402_detected(self):
        exc = _openai_status_error(
            402, {"error": {"message": "Resource exhausted: token limit reached", "type": "gha_proxy"}}
        )
        result = detect_quota_error(exc, provider="openai")
        assert isinstance(result, LLMQuotaError)
        assert isinstance(result, LLMTerminalError)
        assert result.status_code == 402
        assert result.provider == "openai"
        assert result.provider_message == "Resource exhausted: token limit reached"
        assert "HTTP 402" in str(result) and "openai" in str(result) and "stopped" in str(result)
        assert result.telemetry_properties["error_type"] == "quota"
        assert result.telemetry_properties["error_status_code"] == 402

    def test_paywall_402_detected_whatever_its_message(self):
        exc = _openai_status_error(
            402,
            {
                "error": {
                    "message": "Your team used this month's 2,000,000 tokens. Upgrade to keep reviewing.",
                    "type": "gha_proxy",
                    "wall": {"plan": "team", "meter": "tokens", "message": "Your team used this month's tokens."},
                }
            },
        )
        result = detect_quota_error(exc, provider="openai")
        assert result is not None
        assert result.provider_message.startswith("Your team used this month's")

    def test_429_insufficient_quota_detected(self):
        exc = _openai_status_error(
            429,
            {
                "error": {
                    "message": "You exceeded your current plan, please check your billing details.",
                    "type": "insufficient_quota",
                    "code": "insufficient_quota",
                }
            },
        )
        result = detect_quota_error(exc, provider="openai")
        assert result is not None
        assert result.status_code == 429

    def test_plain_429_rate_limit_is_not_quota(self):
        exc = _openai_status_error(
            429,
            {
                "error": {
                    "message": "Rate limit reached for gpt-4o on tokens per min (TPM): Limit 30000. Try again in 2s.",
                    "type": "tokens",
                    "code": "rate_limit_exceeded",
                }
            },
        )
        assert detect_quota_error(exc, provider="openai") is None

    def test_gemini_per_minute_limit_is_not_quota(self):
        # Mentions "quota" but clears within a minute; stopping the run on it would be a regression.
        exc = _openai_status_error(
            429,
            {
                "error": {
                    "code": 429,
                    "message": "Resource has been exhausted (e.g. check quota).",
                    "status": "RESOURCE_EXHAUSTED",
                }
            },
        )
        assert detect_quota_error(exc, provider="google") is None

    def test_anthropic_empty_credit_balance_400_is_quota(self):
        exc = _openai_status_error(
            400,
            {
                "type": "error",
                "error": {
                    "type": "invalid_request_error",
                    "message": "Your credit balance is too low to access the Anthropic API. Please go to Plans & Billing to upgrade or purchase credits.",
                },
            },
        )
        result = detect_quota_error(exc, provider="anthropic")
        assert result is not None
        assert result.status_code == 400

    def test_other_400_is_not_quota(self):
        exc = _openai_status_error(
            400, {"error": {"message": "max_tokens: must be at most 8192", "type": "invalid_request_error"}}
        )
        assert detect_quota_error(exc, provider="anthropic") is None

    def test_wrapped_error_keeps_the_status_of_its_cause(self):
        original = _FakeStatusError("payment required", status_code=402)
        try:
            try:
                raise original
            except _FakeStatusError as inner:
                raise RuntimeError("agent step failed") from inner
        except RuntimeError as wrapped:
            result = detect_quota_error(wrapped, provider="anthropic")
        assert result is not None
        assert result.status_code == 402

    def test_string_only_wrap_detected(self):
        exc = ValueError("Error code: 402 - {'error': {'message': 'out of credits'}}")
        assert detect_quota_error(exc, provider="openai") is not None

    def test_other_failures_are_not_quota(self):
        assert detect_quota_error(TimeoutError("timed out"), provider="openai") is None
        assert detect_quota_error(_FakeStatusError("upstream", status_code=503), provider="openai") is None
        assert detect_quota_error(_FakeStatusError("bad key", status_code=401), provider="openai") is None

    def test_already_typed_passes_through(self):
        original = LLMQuotaError(
            "boom", provider="openai", status_code=402, provider_message="x", telemetry_properties={}
        )
        assert detect_quota_error(original, provider="ignored") is original


def test_raise_if_terminal_llm_error_raises_quota_before_auth():
    error = _openai_status_error(402, {"error": {"message": "Unauthorized: no credits left", "type": "gha_proxy"}})
    with patch("agents.llm_errors.current_provider_key_context", return_value=("openai", "a8dd")):
        with pytest.raises(LLMQuotaError):
            raise_if_terminal_llm_error(error)


def test_raise_if_terminal_llm_error_ignores_rate_limits():
    error = _openai_status_error(429, {"error": {"message": "Rate limit reached", "code": "rate_limit_exceeded"}})
    with patch("agents.llm_errors.current_provider_key_context", return_value=("openai", "a8dd")):
        raise_if_terminal_llm_error(error)
