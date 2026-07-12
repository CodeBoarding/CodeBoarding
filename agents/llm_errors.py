"""Provider-agnostic detection and typing of LLM authentication failures.

A rejected API key (HTTP 401, or a provider's equivalent auth/permission
error) is *permanent* for the run: retrying it wastes minutes of backoff and
floods telemetry with identical ``$exception`` events. The agent retry loop and
the CLI both need to recognize one when they see it, regardless of which SDK
raised it.

``detect_auth_error`` maps a raw provider exception to an :class:`LLMAuthError`
(or ``None`` when it isn't an auth failure). The typed error carries the
provider, a masked key tail, and the provider's own message, and forwards them
as ``telemetry_properties`` so the PostHog ``$exception`` event — and the
dashboard's structured ``error`` columns — populate instead of staying null.
"""

from __future__ import annotations

import re

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


class LLMAuthError(RuntimeError):
    """An LLM provider rejected our credentials (HTTP 401 or equivalent).

    Terminal by construction: the agent retry loop gives up immediately and the
    CLI surfaces an actionable message instead of a traceback. ``provider`` and
    ``key_tail`` identify *which* key to fix without leaking the secret.

    ``telemetry_properties`` is forwarded into the PostHog ``$exception`` event
    (see ``telemetry.events._exception_properties``).
    """

    def __init__(self, message: str, *, provider: str, key_tail: str, telemetry_properties: dict):
        super().__init__(message)
        self.provider = provider
        self.key_tail = key_tail
        self.telemetry_properties = telemetry_properties


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
