"""The stdout JSON contract the IDE, the wrapper and the GitHub Action parse; human text goes to stderr."""

import json
import sys
from typing import Any

from agents.llm_errors import LLMQuotaError, LLMTerminalError


def emit(payload: dict[str, Any]) -> None:
    """Write a wire dict as JSON to stdout."""
    sys.stdout.write(json.dumps(payload, default=str, indent=2, sort_keys=True) + "\n")
    sys.stdout.flush()


def terminal_llm_error_payload(mode: str, error: LLMTerminalError) -> dict[str, Any]:
    """The wire dict for a run stopped by a quota or credential refusal.

    ``requiresFullAnalysis`` is false because a full run would hit the same refusal.
    """
    if isinstance(error, LLMQuotaError):
        kind, status = "llm_quota_exhausted", error.status_code
    else:
        kind, status = "llm_auth", error.telemetry_properties.get("error_status_code")
    return {
        "mode": mode,
        "error": str(error),
        "kind": kind,
        "statusCode": status,
        "provider": error.provider,
        "requiresFullAnalysis": False,
    }
