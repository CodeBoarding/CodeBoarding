"""Analysis lifecycle telemetry: ``analysis_started`` / ``analysis_completed``.

Instrumentation sits on the ``DiagramGenerator`` analysis methods — the
chokepoint every surface (OSS CLI, GitHub Action, hosted, VSCode wrapper) flows
through — so each run is captured regardless of caller; ``source`` tells them
apart. ``partial`` has no generator chokepoint, so ``run_partial`` opens
``track_analysis_run`` directly. No repo name, path, or code is ever sent.
"""

import functools
import time

from contextlib import contextmanager
from contextvars import ContextVar
from importlib.metadata import PackageNotFoundError, version

from telemetry.service import telemetry

# Current run_id, set for the duration of a run so nested emitters (e.g. the
# scanner's ``repo_scanned``) can tag the same id. Concurrency-safe via ContextVar.
_current_run_id: ContextVar[str | None] = ContextVar("telemetry_run_id", default=None)

# True while a run is being measured, so a nested analysis (incremental -> full
# rebuild) is owned by the outer run and does not emit a second event pair.
_analysis_active: ContextVar[bool] = ContextVar("telemetry_analysis_active", default=False)


def _app_version() -> str:
    try:
        return version("codeboarding")
    except PackageNotFoundError:
        return "unknown"


# Repos already reported this process, so a scan that runs twice per analysis
# (StaticAnalyzer init + diagram generation) emits a single event. Paths stay
# in-memory only and are never sent.
_scanned_repos: set[str] = set()


def track_tech_stack(repo_path, total_loc: int, languages) -> None:
    """Emit one ``repo_scanned`` event with lines-of-code and tech stack.

    ``languages`` is duck-typed (``.language``, ``.size``, ``.percentage``). Only
    aggregate language names and line counts are sent — never paths or code.
    """
    key = str(repo_path)
    if key in _scanned_repos:
        return
    _scanned_repos.add(key)

    top = sorted(languages, key=lambda pl: pl.size, reverse=True)
    props = {
        "version": _app_version(),
        "total_loc": total_loc,
        "language_count": len(languages),
        "languages": [
            {"language": pl.language, "loc": pl.size, "percentage": round(pl.percentage, 2)} for pl in top[:15]
        ],
        "stack": ",".join(sorted(pl.language for pl in languages)),
    }
    run_id = _current_run_id.get()
    if run_id is not None:
        props["run_id"] = run_id
    telemetry.capture("repo_scanned", props)


def _token_usage() -> dict:
    """Snapshot of the process-global token counters (best effort).

    Why: ``MONITORING_CALLBACK`` is imported lazily so importing this module (and
    its importers, e.g. the scanner) does not eagerly pull in the LLM/agent stack.
    """
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
def track_analysis_run(
    command: str,
    *,
    run_id: str | None = None,
    depth_level: int | None = None,
    requested_command: str | None = None,
):
    """Emit the ``analysis_started`` / ``analysis_completed`` pair around one run.

    Reports token-usage delta, wall-clock duration, and outcome. Re-entrant: a
    nested run (incremental -> full fallback) is owned by the outer run and emits
    no pair of its own.
    """
    if _analysis_active.get():
        yield {}
        return

    base = {"command": command, "version": _app_version()}
    if run_id is not None:
        base["run_id"] = run_id
    if depth_level is not None:
        base["depth_level"] = depth_level
    if requested_command is not None and requested_command != command:
        base["requested_command"] = requested_command

    before = _token_usage()
    started = time.monotonic()
    telemetry.capture("analysis_started", dict(base))

    run_id_token = _current_run_id.set(run_id)
    active_token = _analysis_active.set(True)

    status = "success"
    error_type: str | None = None
    try:
        yield base
    except BaseException as exc:
        status = "error"
        error_type = type(exc).__name__
        raise
    finally:
        _analysis_active.reset(active_token)
        _current_run_id.reset(run_id_token)
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


def track_generator_analysis(command: str):
    """Decorator: instrument a ``DiagramGenerator`` analysis method as one run.

    Reads ``run_id`` / ``depth_level`` from the generator, so both are always
    present; one application covers every caller of the method.
    """

    def decorator(method):
        @functools.wraps(method)
        def wrapper(self, *args, **kwargs):
            with track_analysis_run(
                command,
                run_id=getattr(self, "run_id", None),
                depth_level=getattr(self, "depth_level", None),
            ):
                return method(self, *args, **kwargs)

        return wrapper

    return decorator
