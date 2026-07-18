"""Analysis lifecycle telemetry, emitted from the core kernels.

Decorating the core (``codeboarding_workflows.analysis``) rather than the CLI
means token usage and run outcomes are captured no matter who invokes the core
(OSS CLI, GitHub Action, or the VSCode wrapper) — the ``source`` property tells
them apart. Exceptions are forwarded to PostHog's built-in ``$exception`` event
so we get structured error tracking instead of hand-rolled trace formatting.
"""

from __future__ import annotations

import functools
import logging
import os
import time

from contextvars import ContextVar
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

from telemetry.schemas import (
    AnalysisCompleted,
    AnalysisStarted,
    LanguageStat,
    LspAnalysisResult,
    RepoScanned,
    TokenSnapshot,
)
from telemetry.service import telemetry

from agents.llm_config import MONITORING_CALLBACK

logger = logging.getLogger(__name__)

# Current analysis run_id, set by ``track_analysis`` for the duration of a run
# so nested emitters (e.g. the scanner's ``repo_scanned``) can tag the same id
# without threading run_id through every call. Concurrency-safe via ContextVar.
_current_run_id: ContextVar[str | None] = ContextVar("telemetry_run_id", default=None)


def _app_version() -> str:
    try:
        return version("codeboarding")
    except PackageNotFoundError:
        return "unknown"


# Repos already reported this process, so a scan that runs twice per analysis
# emits a single event. Paths stay in-memory only and are never sent.
_scanned_repos: set[str] = set()


def _resolve_run_id() -> str | None:
    """Correlation id from the subprocess env, then the run-scoped ContextVar."""
    return os.getenv("CODEBOARDING_RUN_ID") or _current_run_id.get()


def _token_usage() -> TokenSnapshot:
    """Snapshot of the process-global token counters (best effort)."""
    try:
        stats = MONITORING_CALLBACK.stats.to_dict()
        usage = stats.get("token_usage", {})
        return TokenSnapshot(
            model_name=stats.get("model_name"),
            total_tokens=usage.get("total_tokens", 0),
            input_tokens=usage.get("input_tokens", 0),
            output_tokens=usage.get("output_tokens", 0),
        )
    except Exception:
        return TokenSnapshot()


def track_tech_stack(repo_path: str | Path, total_loc: int, languages: list) -> None:
    """Emit one ``repo_scanned`` event with lines-of-code and tech stack."""
    key = str(repo_path)
    if key in _scanned_repos:
        return
    _scanned_repos.add(key)

    top = sorted(languages, key=lambda pl: pl.size, reverse=True)
    event = RepoScanned(
        version=_app_version(),
        run_id=_resolve_run_id(),
        total_loc=total_loc,
        language_count=len(languages),
        languages=[
            LanguageStat(language=pl.language, loc=pl.size, percentage=round(pl.percentage, 2)) for pl in top[:15]
        ],
        stack=",".join(sorted(pl.language for pl in languages)),
    )
    telemetry.capture("repo_scanned", event.model_dump(exclude_none=True))


def track_lsp_result(
    *,
    language: str,
    loc: int,
    status: str,
    duration_ms: int,
    analysis: dict,
    diagnostics: dict,
) -> None:
    """Emit one ``lsp_analysis_result`` event for a language analysis pass."""
    program_graph = analysis.get("program_graph")
    missing_call_graph = program_graph is None
    node_count = len(program_graph.symbols) if program_graph is not None else 0
    edge_count = len(program_graph.call_edges()) if program_graph is not None else 0
    source_files = analysis.get("source_files")
    missing_source_files = source_files is None
    if source_files is None:
        source_files = []
    references = analysis.get("references", [])
    if not diagnostics:
        diagnostics = analysis.get("diagnostics") or {}
    diagnostic_count = sum(len(items) for items in diagnostics.values()) if diagnostics else 0

    zero_nodes_with_loc = loc > 0 and node_count == 0
    zero_edges_with_loc = loc > 0 and edge_count == 0
    missing_summary = missing_call_graph or missing_source_files
    quality_status = "error" if zero_nodes_with_loc else "warning" if zero_edges_with_loc or missing_summary else "ok"

    issues = []
    if zero_nodes_with_loc:
        issues.append("zero nodes despite LOC")
    if zero_edges_with_loc:
        issues.append("zero edges despite LOC")
    if missing_call_graph:
        issues.append("missing call graph")
    if missing_source_files:
        issues.append("missing source files")
    if zero_nodes_with_loc:
        logger.error("LSP analysis result for %s is unhealthy: %s", language, ", ".join(issues))
    elif issues:
        logger.warning(
            "LSP analysis result for %s is degraded: %s",
            language,
            ", ".join(issues),
        )

    event = LspAnalysisResult(
        version=_app_version(),
        run_id=_resolve_run_id(),
        language=language,
        loc=loc,
        status=status,
        duration_ms=duration_ms,
        source_file_count=len(source_files),
        node_count=node_count,
        edge_count=edge_count,
        reference_count=len(references),
        diagnostic_file_count=len(diagnostics),
        diagnostic_count=diagnostic_count,
        quality_status=quality_status,
        zero_nodes_with_loc=zero_nodes_with_loc,
        zero_edges_with_loc=zero_edges_with_loc,
    )
    telemetry.capture("lsp_analysis_result", event.model_dump(exclude_none=True))


def track_analysis(func):
    """Emit ``analysis_started`` / ``analysis_completed`` around a core run.

    ``run_id`` is resolved from the VSCode env var, an explicit keyword, or
    ``self.run_id`` (DiagramGenerator methods). Token counts are reported as the
    delta over this run. On failure the exception is forwarded to PostHog's
    built-in ``$exception`` event and ``analysis_completed`` gets ``status=error``.
    """

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        instance = args[0] if args else None
        command = func.__name__
        run_id = os.getenv("CODEBOARDING_RUN_ID") or kwargs.get("run_id") or getattr(instance, "run_id", None)
        depth_level = kwargs.get("depth_level") or getattr(instance, "depth_level", None)

        telemetry.capture(
            "analysis_started",
            AnalysisStarted(command=command, version=_app_version(), run_id=run_id, depth_level=depth_level).model_dump(
                exclude_none=True
            ),
        )

        before = _token_usage()
        started = time.monotonic()
        # Expose run_id to nested emitters (e.g. the scanner) for this run only.
        run_id_token = _current_run_id.set(run_id)

        status = "success"
        exc: BaseException | None = None
        try:
            return func(*args, **kwargs)
        except BaseException as e:
            status = "error"
            exc = e
            raise
        finally:
            _current_run_id.reset(run_id_token)
            after = _token_usage()
            telemetry.capture(
                "analysis_completed",
                AnalysisCompleted(
                    command=command,
                    version=_app_version(),
                    status=status,
                    duration_ms=round((time.monotonic() - started) * 1000),
                    model_name=after.model_name,
                    total_tokens=after.total_tokens - before.total_tokens,
                    input_tokens=after.input_tokens - before.input_tokens,
                    output_tokens=after.output_tokens - before.output_tokens,
                    run_id=run_id,
                    depth_level=depth_level,
                ).model_dump(exclude_none=True),
            )
            if exc is not None:
                exc_props: dict = {"command": command, "version": _app_version()}
                if run_id:
                    exc_props["run_id"] = run_id
                exc_props.update(_exception_properties(exc))
                telemetry.capture_exception(exc, properties=exc_props)
            telemetry.flush()

    return wrapper


def capture_error(command: str, exc: BaseException, *, extra: dict | None = None) -> None:
    """Forward *exc* to PostHog's built-in ``$exception`` error tracking.

    Thin wrapper around ``telemetry.capture_exception`` that stamps the standard
    properties (command, version, run_id, source). For entry points not wrapped
    by :func:`track_analysis` (the wrapper's full-analysis / expand / git paths).
    """
    properties: dict = {"command": command, "version": _app_version()}
    run_id = _resolve_run_id()
    if run_id is not None:
        properties["run_id"] = run_id
    properties.update(_exception_properties(exc))
    if extra:
        properties.update(extra)
    telemetry.capture_exception(exc, properties=properties)
    telemetry.flush()


def _exception_properties(exc: BaseException) -> dict:
    """Diagnostic properties an exception carries for its ``$exception`` event."""
    props = getattr(exc, "telemetry_properties", None)
    return props if isinstance(props, dict) else {}
