"""Library-layer incremental pipeline.

Owns the orchestration behind semantic incremental analysis:
loading the prior analysis, resolving refs, running the updater, invoking
``DiagramGenerator.generate_analysis_incremental``, and writing metadata.

Callers (Core's CLI; the wrapper's ``AnalysisController``)
construct a ``DiagramGenerator`` the same way they would for a full run and
hand it to :func:`run_incremental_pipeline`. No process boundary involved —
the CLI envelope and JSON emission stay at the caller's edge.
"""

import contextlib
import io
import logging
import subprocess
from collections import defaultdict
from pathlib import Path
from typing import Any

from agents.agent_responses import MethodEntry
from diagram_analysis.diagram_generator import DiagramGenerator
from diagram_analysis.incremental_models import (
    IncrementalRunResult,
    IncrementalSummary,
    IncrementalSummaryKind,
)
from diagram_analysis.incremental_tracer import TraceConfig
from diagram_analysis.incremental_updater import IncrementalUpdater, SymbolResolver
from diagram_analysis.io_utils import load_full_analysis
from diagram_analysis.run_metadata import last_successful_commit, worktree_has_changes, write_last_run_metadata
from repo_utils.change_detector import detect_changes_from_parsed_diff
from repo_utils.ignore import initialize_codeboardingignore
from repo_utils.parsed_diff import load_parsed_git_diff
from repo_utils import get_git_commit_hash, get_repo_state_hash
from static_analyzer.analysis_result import StaticAnalysisResults
from static_analyzer.node import Node
from utils import to_relative_path

logger = logging.getLogger(__name__)


def normalize_repo_path(path: str, repo_dir: Path) -> str:
    return to_relative_path(path.replace("\\", "/"), repo_dir)


def collect_method_entries(static_analysis: StaticAnalysisResults, repo_dir: Path) -> dict[str, list[MethodEntry]]:
    methods_by_file: dict[str, list[MethodEntry]] = defaultdict(list)

    for language in static_analysis.get_languages():
        lang_data = static_analysis.results.get(language, {})
        references = lang_data.get("references", {})
        if isinstance(references, dict):
            ref_values = references.values()
        elif isinstance(references, list):
            ref_values = references
        else:
            continue

        for node in ref_values:
            if not isinstance(node, Node):
                continue
            if node.is_callback_or_anonymous():
                continue
            if not (node.is_callable() or node.is_class()):
                continue

            file_path = normalize_repo_path(str(node.file_path), repo_dir)
            methods_by_file[file_path].append(
                MethodEntry(
                    qualified_name=node.fully_qualified_name,
                    start_line=node.line_start,
                    end_line=node.line_end,
                    node_type=node.type.name,
                )
            )

    for file_path in list(methods_by_file.keys()):
        methods_by_file[file_path] = sorted(
            methods_by_file[file_path],
            key=lambda method: (method.start_line, method.end_line, method.qualified_name),
        )

    return methods_by_file


def build_symbol_resolver(static_analysis: StaticAnalysisResults, repo_dir: Path) -> SymbolResolver:
    methods_by_file = collect_method_entries(static_analysis, repo_dir)

    def resolve(file_path: str) -> list[MethodEntry]:
        normalized = normalize_repo_path(file_path, repo_dir)
        return methods_by_file.get(normalized, [])

    return resolve


# ---------------------------------------------------------------------------
# Orchestration entry point
# ---------------------------------------------------------------------------


def _resolve_source_identity(repo_dir: Path, ref: str | None) -> str:
    if not ref:
        return get_repo_state_hash(repo_dir) if worktree_has_changes(repo_dir) else get_git_commit_hash(repo_dir)

    try:
        result = subprocess.run(
            ["git", "rev-parse", "--verify", ref],
            cwd=repo_dir,
            capture_output=True,
            text=True,
            check=True,
        )
        return result.stdout.strip() or ref
    except subprocess.CalledProcessError:
        logger.warning("Could not resolve source ref '%s'; preserving original value", ref)
        return ref


def _diff_base_for_successful_target(repo_dir: Path, target_ref: str | None, source_identity: str) -> str | None:
    if target_ref:
        return source_identity
    return None if worktree_has_changes(repo_dir) else source_identity


def _full_required_payload(message: str) -> dict[str, Any]:
    result = IncrementalRunResult(
        summary=IncrementalSummary(
            kind=IncrementalSummaryKind.REQUIRES_FULL_ANALYSIS,
            message=message,
            requires_full_analysis=True,
        )
    )
    payload = result.to_dict()
    payload["mode"] = "incremental"
    payload["requiresFullAnalysis"] = True
    return payload


def run_incremental_pipeline(
    generator: DiagramGenerator,
    *,
    base_ref: str | None = None,
    target_ref: str | None = None,
) -> dict[str, Any]:
    """Run a semantic incremental analysis against a prepared ``DiagramGenerator``.

    Mirrors the full-analysis pattern: the caller constructs the generator
    (with whatever ``StaticAnalyzer`` attachment is appropriate for its
    context) and hands it in. This function owns the orchestration —
    loading the prior analysis, resolving refs, running the updater,
    invoking ``generator.generate_analysis_incremental``, and writing
    ``incremental_run_metadata.json`` on success.

    Returns the wire-format dict that both Core's CLI and the wrapper's
    JSON-RPC layer surface unchanged to their callers.
    """
    repo_path = Path(generator.repo_location).resolve()
    output_dir = Path(generator.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    initialize_codeboardingignore(output_dir)

    existing = load_full_analysis(output_dir)
    if existing is None:
        return _full_required_payload("No existing analysis.json; full analysis required.")

    resolved_base_ref = base_ref or last_successful_commit(output_dir)
    if not resolved_base_ref:
        return _full_required_payload(
            "No prior incremental metadata found; full analysis required before running incremental."
        )

    resolved_target_ref = target_ref or ""
    parsed_diff = load_parsed_git_diff(repo_path, resolved_base_ref, resolved_target_ref)
    if parsed_diff.error:
        return _full_required_payload(f"Git diff failed for incremental analysis: {parsed_diff.error}")

    changes = detect_changes_from_parsed_diff(parsed_diff)
    analysis_path = output_dir / "analysis.json"
    if changes.is_empty():
        source_identity = _resolve_source_identity(repo_path, resolved_target_ref)
        write_last_run_metadata(
            output_dir,
            repo_path,
            mode="incremental",
            analysis_path=analysis_path,
            commit_hash=source_identity,
            source_identity=source_identity,
            diff_base_ref=_diff_base_for_successful_target(repo_path, resolved_target_ref, source_identity),
        )
        result = IncrementalRunResult(
            summary=IncrementalSummary(
                kind=IncrementalSummaryKind.NO_CHANGES,
                message="No file changes detected.",
            ),
            analysis_path=analysis_path,
        )
        payload = result.to_dict()
        payload.update(
            {
                "mode": "incremental",
                "requiresFullAnalysis": False,
                "baseRef": resolved_base_ref,
                "targetRef": resolved_target_ref or "WORKTREE",
                "resolvedTargetCommit": source_identity,
                "changeSet": changes.to_dict(),
                "metadataPath": str(output_dir / "incremental_run_metadata.json"),
            }
        )
        return payload

    root_analysis, _sub_analyses = existing
    if root_analysis is None:
        return _full_required_payload("No root analysis present; full analysis required.")

    with contextlib.redirect_stdout(io.StringIO()):
        generator.pre_analysis()

    if generator.static_analysis is None:
        return _full_required_payload("Static analysis could not be initialized; full analysis required.")

    symbol_resolver = build_symbol_resolver(generator.static_analysis, repo_path)

    updater = IncrementalUpdater(
        root_analysis,
        symbol_resolver=symbol_resolver,
        repo_dir=repo_path,
    )
    delta = updater.compute_delta(
        added_files=changes.added_files,
        modified_files=changes.modified_files,
        deleted_files=changes.deleted_files,
        changes=changes,
    )

    trace_config = TraceConfig()
    with contextlib.redirect_stdout(io.StringIO()):
        incremental_result = generator.generate_analysis_incremental(
            delta=delta,
            base_ref=resolved_base_ref,
            parsed_diff=parsed_diff,
            config=trace_config,
        )

    source_identity = _resolve_source_identity(repo_path, resolved_target_ref)
    payload = incremental_result.to_dict()
    payload.update(
        {
            "mode": "incremental",
            "requiresFullAnalysis": incremental_result.summary.requires_full_analysis,
            "baseRef": resolved_base_ref,
            "targetRef": resolved_target_ref or "WORKTREE",
            "resolvedTargetCommit": source_identity,
            "changeSet": changes.to_dict(),
            "incrementalDelta": delta.to_dict(),
        }
    )

    if not incremental_result.summary.requires_full_analysis and incremental_result.analysis_path is not None:
        write_last_run_metadata(
            output_dir,
            repo_path,
            mode="incremental",
            analysis_path=incremental_result.analysis_path,
            commit_hash=source_identity,
            source_identity=source_identity,
            diff_base_ref=_diff_base_for_successful_target(repo_path, resolved_target_ref, source_identity),
        )
        payload["metadataPath"] = str(output_dir / "incremental_run_metadata.json")

    return payload
