"""Tests for provider-agnostic LLM auth-error detection and typing."""

import os
from unittest.mock import patch

import botocore.exceptions
import httpx
import openai
import pytest

from agents.llm_config import CODEBOARDING_KEY_TAIL, OIDC_RELAY_PLACEHOLDER_KEY, current_provider_key_context
from agents.llm_errors import (
    LLMAuthError,
    LLMQueryError,
    ScopeContextTooLargeError,
    detect_auth_error,
    is_transient_llm_error,
    raise_if_auth_error,
    retry_after_seconds,
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


class TestExplicitStatusWinsOverTheBody:
    """Why: every one of these reached users as "your API key was rejected" — 26 events from real runs.
    OpenRouter embeds each failed upstream attempt in ``previous_errors``, so a body can carry
    ``'code': 401`` while the response itself is a 400 or a 503."""

    def test_oversized_prompt_with_an_upstream_401_is_not_auth(self):
        exc = _FakeStatusError(
            "Error code: 400 - {'error': {'message': 'Provider returned error', 'code': 400, 'metadata': "
            "{'raw': 'The input token count exceeds the maximum number of tokens allowed 1048576.', "
            "'previous_errors': [{'code': 401, 'message': 'Provider returned error'}]}}}",
            status_code=400,
        )
        assert detect_auth_error(exc, provider="openrouter", key_tail=CODEBOARDING_KEY_TAIL) is None

    def test_overloaded_model_with_an_upstream_401_is_not_auth(self):
        """A 503 is transient; typed as auth it would end the run instead of being retried."""
        exc = _FakeStatusError(
            "Error code: 503 - {'error': {'message': 'This model is currently experiencing high demand.', "
            "'metadata': {'previous_errors': [{'code': 401}]}}}",
            status_code=503,
        )
        assert detect_auth_error(exc, provider="openrouter", key_tail="k3y5") is None

    def test_account_in_arrears_is_not_auth(self):
        """``access denied`` in the text, but the key is fine: the account has an unpaid balance."""
        exc = _FakeStatusError(
            "Error code: 400 - {'error': {'message': 'Access denied, please make sure your account is in good "
            "standing.', 'type': 'Arrearage', 'code': 'Arrearage'}}",
            status_code=400,
        )
        assert detect_auth_error(exc, provider="openai", key_tail="qLUW") is None

    def test_google_invalid_key_without_a_status_is_still_auth(self):
        """The shape Google's bad keys arrive in: langchain's wrapper exposes no status, so the text decides."""
        exc = RuntimeError(
            "Error calling model 'gemini-3-flash-preview' (INVALID_ARGUMENT): 400 INVALID_ARGUMENT. "
            "{'error': {'code': 400, 'message': 'API key not valid. Please pass a valid API key.'}}"
        )
        assert detect_auth_error(exc, provider="google", key_tail="1234") is not None


class TestHostedCredentials:
    def test_the_relay_placeholder_names_no_key(self):
        with patch.dict(
            os.environ,
            {"OPENROUTER_API_KEY": OIDC_RELAY_PLACEHOLDER_KEY, "OPENROUTER_BASE_URL": "http://127.0.0.1:4000"},
            clear=True,
        ):
            assert current_provider_key_context() == ("openrouter", CODEBOARDING_KEY_TAIL)

    def test_a_rejected_hosted_run_points_at_the_workflow_not_a_key(self):
        """Why: users were told to verify a key ending in '…elay' — the tail of the placeholder."""
        result = detect_auth_error(
            _FakeStatusError("Invalid GitHub OIDC token.", status_code=401),
            provider="openrouter",
            key_tail=CODEBOARDING_KEY_TAIL,
        )
        assert result is not None
        assert "hosted" in str(result)
        assert "id-token: write" in str(result)
        assert "Verify the key" not in str(result)

    def test_a_403_shows_the_providers_reason_not_a_key_hint(self):
        """Why: OpenRouter refused single requests with a 403 while the same hosted key served the rest, and the
        hint to grant `id-token: write` hid the reason."""
        result = detect_auth_error(
            _FakeStatusError(
                "Error code: 403 - {'error': {'message': 'Input flagged by moderation'}}", status_code=403
            ),
            provider="openrouter",
            key_tail=CODEBOARDING_KEY_TAIL,
        )
        assert result is not None
        assert "HTTP 403" in str(result)
        assert "Input flagged by moderation" in str(result)
        assert "id-token" not in str(result)


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


def test_raise_if_auth_error_uses_current_provider_context():
    error = _FakeStatusError("invalid api key", status_code=401)
    with patch("agents.llm_errors.current_provider_key_context", return_value=("google", "1234")):
        with pytest.raises(LLMAuthError, match="google") as caught:
            raise_if_auth_error(error)

    assert caught.value.key_tail == "1234"


@pytest.mark.parametrize(
    ("error", "error_type"),
    [
        (_FakeStatusError("Resource exhausted: token limit reached", status_code=402), "quota"),
        (_FakeStatusError("Error code: 429 - {'code': 'insufficient_quota'}", status_code=429), "quota"),
        (_FakeStatusError("Rate limit reached, try again in 20s", status_code=429), "llm"),
        (TimeoutError("provider did not answer"), "llm"),
    ],
)
def test_llm_failure_properties_tells_quota_apart(error: Exception, error_type: str):
    with patch("agents.llm_errors.current_provider_key_context", return_value=("openai", "1234")):
        query_error = LLMQueryError(error)
        props = query_error.telemetry_properties

    assert query_error.is_quota == (error_type == "quota")
    assert not query_error.is_codeboarding_quota

    assert props["error_type"] == error_type
    assert props["error_provider"] == "openai"
    assert props["error_status_code"] == getattr(error, "status_code", None)


@pytest.mark.parametrize("hosted", [False, True])
@pytest.mark.parametrize(
    "status,body,codeboarding_quota",
    [
        (402, {"type": "gha_proxy", "message": "Resource exhausted: token limit reached"}, True),
        (402, {"message": "Insufficient upstream credits"}, False),
        (402, {"type": "provider", "message": "Resource exhausted: token limit reached"}, False),
        (402, {"type": "gha_proxy", "message": "Insufficient upstream credits"}, False),
        (429, {"code": "insufficient_quota", "message": "Balance empty"}, False),
    ],
)
def test_query_error_owns_quota_guidance(hosted, status, body, codeboarding_quota):
    error = openai.APIStatusError(
        body["message"],
        response=httpx.Response(status, request=httpx.Request("POST", "https://proxy.example/chat/completions")),
        body=body,
    )
    key_tail = CODEBOARDING_KEY_TAIL if hosted else "1234"
    with patch("agents.llm_errors.current_provider_key_context", return_value=("openrouter", key_tail)):
        query_error = LLMQueryError(error)

    assert query_error.is_quota
    assert query_error.is_codeboarding_quota == codeboarding_quota
    assert query_error.telemetry_properties["error_message"] == str(error)
    message = str(query_error)
    if codeboarding_quota:
        assert "https://codeboarding.org/pricing" in message
        assert "weekly reset" in message
        assert "service issue" not in message
    elif hosted:
        assert "CodeBoarding service issue" in message
        assert "try again later or contact CodeBoarding support" in message
        assert "Add credits" not in message
        assert "https://codeboarding.org/pricing" not in message
    else:
        assert "Add credits or raise the quota for this provider" in message
        assert "service issue" not in message
        assert "https://codeboarding.org/pricing" not in message


class TestOversizedPromptTelemetry:
    """Why: an oversized prompt is ours to fix, so the dashboard should count it apart from provider failures."""

    def test_a_provider_overflow_is_typed_context(self):
        exc = _FakeStatusError(
            "Error code: 400 - The input token count exceeds the maximum number of tokens allowed 1048576.",
            status_code=400,
        )
        assert LLMQueryError.llm_failure_properties(exc)["error_type"] == "context"

    def test_a_refused_scope_is_typed_context(self):
        assert (
            LLMQueryError.llm_failure_properties(ScopeContextTooLargeError("root", 900_000, 524_288))["error_type"]
            == "context"
        )

    def test_quota_still_wins(self):
        assert (
            LLMQueryError.llm_failure_properties(_FakeStatusError("Resource exhausted", status_code=402))["error_type"]
            == "quota"
        )


_REQUEST = httpx.Request("POST", "https://proxy.example/v1/chat/completions")


def _openai_status_error(cls: type[openai.APIStatusError], status: int, body: dict, headers: dict | None = None):
    response = httpx.Response(status, headers=headers or {}, request=_REQUEST)
    return cls(f"Error code: {status} - {body}", response=response, body=body)


def _bedrock_error(code: str, status: int) -> botocore.exceptions.ClientError:
    response = {"Error": {"Code": code, "Message": code}, "ResponseMetadata": {"HTTPStatusCode": status}}
    return botocore.exceptions.ClientError(response, "Converse")  # type: ignore[arg-type]


class TestTransientLLMError:
    """Why: one throttled call from the hosted proxy used to fail a whole run; a permanent failure must not be retried."""

    @pytest.mark.parametrize(
        "error",
        [
            _openai_status_error(
                openai.RateLimitError,
                429,
                {"Reason": "ConcurrentInvocationLimitExceeded", "Type": "User", "message": "Rate Exceeded."},
            ),
            _openai_status_error(
                openai.InternalServerError, 502, {"error": {"message": "Upstream request failed.", "type": "gha_proxy"}}
            ),
            _FakeStatusError("Service Unavailable", status_code=503),
            _FakeStatusError("Request Timeout", status_code=408),
            _FakeGoogleError("429 Resource has been exhausted (e.g. check quota).", code=429),
            openai.APIConnectionError(request=_REQUEST),
            openai.APITimeoutError(request=_REQUEST),
            httpx.ConnectError("connection refused"),
            TimeoutError("provider did not answer"),
            _bedrock_error("ThrottlingException", 429),
            _bedrock_error("ServiceUnavailableException", 503),
            botocore.exceptions.EndpointConnectionError(endpoint_url="https://bedrock-runtime.us-east-1.amazonaws.com"),
            botocore.exceptions.ReadTimeoutError(endpoint_url="https://bedrock-runtime.us-east-1.amazonaws.com"),
        ],
    )
    def test_retries_throttling_server_errors_and_lost_connections(self, error: Exception):
        assert is_transient_llm_error(error)

    @pytest.mark.parametrize(
        "error",
        [
            _openai_status_error(
                openai.RateLimitError, 429, {"error": {"code": "insufficient_quota", "type": "insufficient_quota"}}
            ),
            # The quota code only on the exception, not in its message.
            openai.RateLimitError(
                "You exceeded your current quota.",
                response=httpx.Response(429, request=_REQUEST),
                body={"code": "insufficient_quota"},
            ),
            _bedrock_error("ValidationException", 400),
            _FakeStatusError("Resource exhausted: token limit reached", status_code=402),
            _openai_status_error(openai.AuthenticationError, 401, {"error": {"message": "invalid api key"}}),
            _FakeStatusError("permission denied", status_code=403),
            _openai_status_error(openai.BadRequestError, 400, {"error": {"message": "invalid tool schema"}}),
            _FakeStatusError("not found", status_code=404),
            ValueError("planner returned malformed JSON"),
        ],
    )
    def test_never_retries_auth_quota_or_request_errors(self, error: Exception):
        assert not is_transient_llm_error(error)

    def test_reads_retry_after_seconds(self):
        error = _openai_status_error(openai.RateLimitError, 429, {}, headers={"Retry-After": "7"})
        assert retry_after_seconds(error) == 7.0

    def test_reads_retry_after_ms(self):
        error = _openai_status_error(openai.RateLimitError, 429, {}, headers={"retry-after-ms": "1500"})
        assert retry_after_seconds(error) == 1.5

    def test_reads_retry_after_http_date(self):
        error = _openai_status_error(
            openai.RateLimitError, 429, {}, headers={"Retry-After": "Wed, 21 Oct 2026 07:28:00 GMT"}
        )
        with patch("agents.llm_errors.time.time", return_value=1792567660.0):  # 07:27:40 GMT that day
            assert retry_after_seconds(error) == 20.0

    @pytest.mark.parametrize(
        "error",
        [
            _openai_status_error(openai.RateLimitError, 429, {}),
            _openai_status_error(
                openai.RateLimitError, 429, {}, headers={"Retry-After": "Wed, 21 Oct 2026 07:27:00 GMT"}
            ),
            _openai_status_error(openai.RateLimitError, 429, {}, headers={"Retry-After": "soon"}),
            _FakeStatusError("Rate limit reached", status_code=429),
            _bedrock_error("ThrottlingException", 429),
        ],
    )
    def test_no_retry_after_when_absent_unreadable_or_past(self, error: Exception):
        with patch("agents.llm_errors.time.time", return_value=1792567660.0):
            assert retry_after_seconds(error) is None
