"""Provider-agnostic detection and typing of terminal LLM failures.

A rejected API key (HTTP 401, or a provider's equivalent auth/permission
error) and an exhausted token or credit quota (HTTP 402, or a 429 that says
the quota is gone) are *permanent* for the run: retrying wastes minutes of
backoff and floods telemetry with identical ``$exception`` events, and falling
back to deterministic names hands over a diagram that looks complete but is not.
The agent retry loop, the diagram generator and the CLI all need to recognize
one when they see it, regardless of which SDK raised it.

``detect_auth_error`` and ``detect_quota_error`` map a raw provider exception to
a typed :class:`LLMTerminalError` (or ``None``). The typed error carries the
provider and the provider's own message, and forwards them as
``telemetry_properties`` so the PostHog ``$exception`` event, and the
dashboard's structured ``error`` columns, populate instead of staying null.
"""

from __future__ import annotations

import logging
import re

from agents.llm_config import current_provider_key_context

logger = logging.getLogger(__name__)

# Process exit code for a rejected key. Distinct from 1 (generic failure) so the
# OSS CLI and the wrapper subprocess both signal "fix your key" the same way, and
# callers/CI can branch on it. Lives here (not in main.py) so the wrapper can
# import it without pulling in Core's whole CLI module.
EXIT_AUTH_ERROR = 2

# Process exit code for an exhausted token or credit quota: the key is fine, the
# account is not, so the caller needs a different remedy than for EXIT_AUTH_ERROR.
EXIT_QUOTA_EXHAUSTED = 3

# Class names, across SDKs, that always mean "credentials were rejected".
# openai/anthropic/cerebras raise ``AuthenticationError``; google raises
# ``Unauthenticated``/``PermissionDenied``; Bedrock (botocore) surfaces
# ``AccessDeniedException``/``UnrecognizedClientException`` in the error text.
_AUTH_TYPE_NAMES = {
    "AuthenticationError",
    "Unauthenticated",
    "PermissionDeniedError",
    "AccessDeniedException",
    "UnrecognizedClientException",
}

# Substrings in a provider's message that indicate an auth failure even when the
# status code isn't exposed on the exception (e.g. errors re-wrapped by langchain
# or botocore, where only the string survives).
_AUTH_MESSAGE_PATTERNS = (
    re.compile(r"\b401\b"),
    re.compile(r"invalid[\s_-]*x?[\s_-]*api[\s_-]*key", re.IGNORECASE),
    re.compile(r"incorrect api key", re.IGNORECASE),
    re.compile(r"api key.*invalid", re.IGNORECASE),
    re.compile(r"authentication[\s_]*error", re.IGNORECASE),
    re.compile(r"authentication fails", re.IGNORECASE),
    re.compile(r"\bunauthorized\b", re.IGNORECASE),
    re.compile(r"missing authentication", re.IGNORECASE),
    re.compile(r"access denied", re.IGNORECASE),
    re.compile(r"invalid github oidc token", re.IGNORECASE),
)

# 402 Payment Required always means the quota is gone. The hosted proxy's paywall
# varies the message per plan, so the status, not the wording, is the signal.
_QUOTA_STATUS = 402
_RATE_LIMIT_STATUS = 429

# A 429 is usually a transient rate limit worth retrying, so only billing-level wording
# counts. Not bare "quota": Gemini's per-minute limit says "Resource has been exhausted
# (e.g. check quota)" and must keep falling back rather than stop the run.
_QUOTA_MARKER = re.compile(
    r"insufficient[\s_]*quota|exceeded your current quota|billing|credit balance|(?:out of|no) credits",
    re.IGNORECASE,
)

# Anthropic reports an empty balance as a 400, not a 402.
_CREDIT_BALANCE_STATUS = 400
_CREDIT_BALANCE_MARKER = re.compile(r"credit balance is too low", re.IGNORECASE)

# openai's APIStatusError renders as "Error code: 402 - {...}"; a re-wrap that keeps
# only the string still carries it.
_QUOTA_STATUS_TEXT = re.compile(r"error code:?\s*402\b", re.IGNORECASE)

# How far down ``__cause__``/``__context__`` to look for the provider's own error.
_MAX_CAUSE_DEPTH = 5


class LLMTerminalError(RuntimeError):
    """An LLM failure no retry or fallback can fix, so the run stops instead of degrading.

    ``telemetry_properties`` is forwarded into the PostHog ``$exception`` event
    (see ``telemetry.events._exception_properties``).
    """

    def __init__(self, message: str, *, provider: str, telemetry_properties: dict):
        super().__init__(message)
        self.provider = provider
        self.telemetry_properties = telemetry_properties


class LLMAuthError(LLMTerminalError):
    """An LLM provider rejected our credentials (HTTP 401 or equivalent).

    The agent retry loop gives up immediately and the CLI surfaces an actionable
    message instead of a traceback. ``provider`` and ``key_tail`` identify *which*
    key to fix without leaking the secret.
    """

    def __init__(self, message: str, *, provider: str, key_tail: str, telemetry_properties: dict):
        super().__init__(message, provider=provider, telemetry_properties=telemetry_properties)
        self.key_tail = key_tail


class LLMQuotaError(LLMTerminalError):
    """An LLM provider refused because the token or credit quota is exhausted (HTTP 402 or equivalent)."""

    def __init__(
        self,
        message: str,
        *,
        provider: str,
        status_code: int | None,
        provider_message: str,
        telemetry_properties: dict,
    ):
        super().__init__(message, provider=provider, telemetry_properties=telemetry_properties)
        self.status_code = status_code
        self.provider_message = provider_message


def detect_auth_error(exc: BaseException, *, provider: str, key_tail: str) -> LLMAuthError | None:
    """Return an :class:`LLMAuthError` if *exc* is an auth failure, else ``None``.

    Already-typed :class:`LLMAuthError` instances pass through unchanged so a
    re-raise doesn't wrap them twice or lose their original provenance.
    """
    if isinstance(exc, LLMAuthError):
        return exc
    if not _is_auth_failure(exc):
        return None

    status = _status_code(exc)
    provider_message = str(exc)
    friendly = (
        f"Your {provider} API key was rejected"
        + (f" (HTTP {status})" if status is not None else "")
        + f". Verify the key ending in '…{key_tail}' and try again."
    )
    telemetry_properties = {
        "error_type": "auth",
        "error_provider": provider,
        "error_status_code": status,
        "error_key_tail": key_tail,
        "error_message": provider_message[:500],
    }
    return LLMAuthError(
        friendly,
        provider=provider,
        key_tail=key_tail,
        telemetry_properties=telemetry_properties,
    )


def detect_quota_error(exc: BaseException, *, provider: str) -> LLMQuotaError | None:
    """Return an :class:`LLMQuotaError` if *exc* is an exhausted quota, else ``None``.

    A plain 429 rate limit is not a quota error: it clears on its own and keeps
    today's retry and fallback.
    """
    if isinstance(exc, LLMQuotaError):
        return exc
    status = _quota_status(exc)
    if status is None:
        return None

    provider_message = _provider_message(exc)
    friendly = (
        f"The {provider} LLM provider refused the request because the token or credit quota is exhausted "
        f"(HTTP {status}): {provider_message.rstrip('.')}. The run was stopped rather than producing a diagram without "
        "AI naming."
    )
    telemetry_properties = {
        "error_type": "quota",
        "error_provider": provider,
        "error_status_code": status,
        "error_message": provider_message[:500],
    }
    return LLMQuotaError(
        friendly,
        provider=provider,
        status_code=status,
        provider_message=provider_message,
        telemetry_properties=telemetry_properties,
    )


def raise_if_terminal_llm_error(exc: Exception) -> None:
    """Raise a typed terminal error when a provider refused for good (quota or credentials)."""
    provider, key_tail = current_provider_key_context()
    # Quota first: a 402 body can carry wording the auth patterns would also match.
    terminal: LLMTerminalError | None = detect_quota_error(exc, provider=provider)
    if terminal is None:
        terminal = detect_auth_error(exc, provider=provider, key_tail=key_tail)
    if terminal is not None:
        logger.error("Terminal LLM failure, not retrying: %s", terminal)
        raise terminal from exc


def _status_code(exc: BaseException) -> int | None:
    code = getattr(exc, "status_code", None)
    if code is None:
        code = getattr(exc, "code", None)  # google GoogleAPICallError.code
    if code is None:
        return None
    try:
        return int(code)
    except (TypeError, ValueError):
        return None


def _is_auth_failure(exc: BaseException) -> bool:
    """True when *exc* represents rejected credentials, across providers."""
    if _status_code(exc) in (401, 403):
        return True
    if type(exc).__name__ in _AUTH_TYPE_NAMES:
        return True
    text = str(exc)
    return any(p.search(text) for p in _AUTH_MESSAGE_PATTERNS)


def _cause_chain(exc: BaseException) -> list[BaseException]:
    """*exc* and the errors it wraps: langchain and SDK re-raises keep the status on the original."""
    chain: list[BaseException] = []
    current: BaseException | None = exc
    while current is not None and current not in chain and len(chain) < _MAX_CAUSE_DEPTH:
        chain.append(current)
        current = current.__cause__ or current.__context__
    return chain


def _quota_status(exc: BaseException) -> int | None:
    """The HTTP status that makes *exc* a quota failure, or ``None`` when it is not one."""
    for error in _cause_chain(exc):
        status = _status_code(error)
        if status is None:
            response_status = getattr(getattr(error, "response", None), "status_code", None)
            status = response_status if isinstance(response_status, int) else None
        if status == _QUOTA_STATUS:
            return status
        if status == _RATE_LIMIT_STATUS and _QUOTA_MARKER.search(_error_text(error)):
            return status
        if status == _CREDIT_BALANCE_STATUS and _CREDIT_BALANCE_MARKER.search(_error_text(error)):
            return status
        if status is None and _QUOTA_STATUS_TEXT.search(str(error)):
            return _QUOTA_STATUS
    return None


def _error_text(exc: BaseException) -> str:
    """Everything a provider may have said about an error: message, body, code and type."""
    parts = [str(exc), repr(getattr(exc, "body", "") or "")]
    parts.extend(str(getattr(exc, name, "") or "") for name in ("code", "type"))
    return " ".join(parts)


def _provider_message(exc: BaseException) -> str:
    """The provider's own sentence (``error.message`` in the body) rather than the SDK's rendering."""
    for error in _cause_chain(exc):
        body = getattr(error, "body", None)
        if isinstance(body, dict):
            inner = body.get("error")
            message = (inner if isinstance(inner, dict) else body).get("message")
            if isinstance(message, str) and message.strip():
                return message.strip()
    return str(exc)
