"""Analysis lifecycle telemetry, emitted from the core kernels.

Decorating the core (``codeboarding_workflows.analysis``) rather than the CLI
means token usage and run outcomes are captured no matter who invokes the core
(OSS CLI, GitHub Action, or the VSCode wrapper) — the ``source`` property tells
them apart. No repository name, path, or code content is ever sent.
"""

import functools
import inspect
import time
from importlib.metadata import PackageNotFoundError, version

from telemetry.service import telemetry


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

    ``languages`` is the scanner's list of detected languages (duck-typed:
    ``.language``, ``.size``, ``.percentage``). Only aggregate language names and
    line counts are sent — never file names, paths, or code.
    """
    key = str(repo_path)
    if key in _scanned_repos:
        return
    _scanned_repos.add(key)

    top = sorted(languages, key=lambda pl: pl.size, reverse=True)
    telemetry.capture(
        "repo_scanned",
        {
            "version": _app_version(),
            "total_loc": total_loc,
            "language_count": len(languages),
            "languages": [
                {"language": pl.language, "loc": pl.size, "percentage": round(pl.percentage, 2)} for pl in top[:15]
            ],
            "stack": ",".join(sorted(pl.language for pl in languages)),
        },
    )


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


def _depth_level(func, args, kwargs) -> int | None:
    """Pull ``depth_level`` from the call if the wrapped function takes one."""
    try:
        bound = inspect.signature(func).bind(*args, **kwargs)
        bound.apply_defaults()
        return bound.arguments.get("depth_level")
    except Exception:
        return None


def track_analysis(func):
    """Emit ``analysis_started`` / ``analysis_completed`` around a core run.

    The command is the wrapped function's name without the ``run_`` prefix
    (``run_full`` -> ``full``). Token counts are reported as the delta over this
    run so a multi-repo loop in one process attributes usage to the right run.
    """

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        base = {"command": func.__name__.removeprefix("run_"), "version": _app_version()}
        depth_level = _depth_level(func, args, kwargs)
        if depth_level is not None:
            base["depth_level"] = depth_level

        before = _token_usage()
        started = time.monotonic()
        telemetry.capture("analysis_started", base)

        status = "success"
        error_type: str | None = None
        try:
            return func(*args, **kwargs)
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

    return wrapper
