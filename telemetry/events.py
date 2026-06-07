"""Analysis lifecycle telemetry, emitted from the core kernels.

Wrapping the core (``codeboarding_workflows.analysis``) rather than the CLI
means token usage and run outcomes are captured no matter who invokes the core
(OSS CLI, GitHub Action, or the VSCode wrapper) — the ``source`` property tells
them apart. No repository name, path, or code content is ever sent.
"""

import time
from contextlib import contextmanager
from importlib.metadata import PackageNotFoundError, version

from telemetry.service import telemetry


def _app_version() -> str:
    try:
        return version("codeboarding")
    except PackageNotFoundError:
        return "unknown"


def _token_usage() -> dict:
    """Snapshot of the process-global token counters (best effort)."""
    try:
        from agents.llm_config import MONITORING_CALLBACK

        stats = MONITORING_CALLBACK.stats.to_dict()
        usage = stats.get("token_usage", {})
        return {
            "model_name": stats.get("model_name"),
            "total_tokens": usage.get("total_tokens", 0),
            "input_tokens": usage.get("input_tokens", 0),
            "output_tokens": usage.get("output_tokens", 0),
        }
    except Exception:
        return {}


@contextmanager
def track_analysis(command: str, depth_level: int | None = None):
    """Emit ``analysis_started`` / ``analysis_completed`` around a core run.

    Token counts are reported as the delta over this run so a multi-repo loop in
    one process attributes usage to the right run.
    """
    base = {"command": command, "version": _app_version()}
    if depth_level is not None:
        base["depth_level"] = depth_level

    before = _token_usage()
    started = time.monotonic()
    telemetry.capture("analysis_started", base)

    status = "success"
    error_type: str | None = None
    try:
        yield
    except BaseException as exc:
        status = "error"
        error_type = type(exc).__name__
        raise
    finally:
        after = _token_usage()
        props = {
            **base,
            "status": status,
            "duration_ms": round((time.monotonic() - started) * 1000),
            "model_name": after.get("model_name"),
            "total_tokens": after.get("total_tokens", 0) - before.get("total_tokens", 0),
            "input_tokens": after.get("input_tokens", 0) - before.get("input_tokens", 0),
            "output_tokens": after.get("output_tokens", 0) - before.get("output_tokens", 0),
        }
        if error_type is not None:
            props["error_type"] = error_type
        telemetry.capture("analysis_completed", props)
        telemetry.flush()
