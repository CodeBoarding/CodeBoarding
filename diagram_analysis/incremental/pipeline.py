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

from agents.agent_responses import MethodEntry
from diagram_analysis.diagram_generator import DiagramGenerator
from diagram_analysis.incremental.payload import (
    FullAnalysisRequiredPayload,
    IncrementalCompletedPayload,
    IncrementalRunPayload,
    NoChangesPayload,
)
from diagram_analysis.incremental.updater import IncrementalUpdater
from diagram_analysis.io_utils import load_full_analysis
from diagram_analysis.run_metadata import last_successful_commit, worktree_has_changes, write_last_run_metadata
from repo_utils.change_detector import ChangeType, detect_changes_from_parsed_diff
from repo_utils.ignore import initialize_codeboardingignore
from repo_utils.parsed_diff import get_parsed_git_diff
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


class StaticAnalysisSymbolResolver:
    """Resolve file paths to their current ``MethodEntry`` list from a static analysis snapshot."""

    def __init__(self, static_analysis: StaticAnalysisResults, repo_dir: Path) -> None:
        self._repo_dir = repo_dir
        self._methods_by_file = collect_method_entries(static_analysis, repo_dir)

    def __call__(self, file_path: str) -> list[MethodEntry]:
        return self.resolve(file_path)

    def resolve(self, file_path: str) -> list[MethodEntry]:
        normalized = normalize_repo_path(file_path, self._repo_dir)
        return self._methods_by_file.get(normalized, [])


# ---------------------------------------------------------------------------
# Orchestration entry point
# ---------------------------------------------------------------------------


def _resolve_source_identity(repo_dir: Path, ref: str | None) -> str:
    """Return a stable identifier for the source tree being analyzed.

    Why: a dirty worktree has no commit hash, so we fall back to a content
    hash (``get_repo_state_hash``) so later incremental runs can still match.
    When *ref* is given, we resolve it to its commit SHA.
    """
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


def _target_ref_matches_checkout(repo_dir: Path, target_ref: str) -> bool:
    """Whether ``target_ref`` resolves to the worktree's current HEAD.

    Why: the static analysis and source reads always run against the worktree,
    so if the caller pins a target ref that isn't checked out, the patch would
    be built from the wrong sources.
    """
    try:
        head = subprocess.run(
            ["git", "rev-parse", "--verify", "HEAD"],
            cwd=repo_dir,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        target = subprocess.run(
            ["git", "rev-parse", "--verify", target_ref],
            cwd=repo_dir,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return False
    return bool(head) and head == target


def _validate_target_ref(repo_path: Path, resolved_target_ref: str) -> str | None:
    """Return an error message if *resolved_target_ref* is incompatible with the current checkout."""
    if not resolved_target_ref:
        return None
    if not _target_ref_matches_checkout(repo_path, resolved_target_ref):
        return (
            f"--target-ref {resolved_target_ref!r} does not match the current checkout; "
            "check out the target ref or omit --target-ref."
        )
    if worktree_has_changes(repo_path):
        return (
            "--target-ref cannot be combined with a dirty worktree; "
            "commit or stash local changes, or omit --target-ref."
        )
    return None


def run_incremental_pipeline(
    generator: DiagramGenerator,
    base_ref: str | None = None,
    target_ref: str | None = None,
) -> IncrementalRunPayload:
    """Run a semantic incremental analysis against a prepared ``DiagramGenerator``.

    ``generator.repo_location`` and ``generator.output_dir`` must already be
    absolute paths (the CLI's ``resolve_local_run_paths`` handles this). The
    function owns the orchestration: loads the prior analysis, resolves refs,
    runs the updater, invokes ``generator.generate_analysis_incremental``, and
    writes ``incremental_run_metadata.json`` on success.

    Returns an ``IncrementalRunPayload`` variant. Callers that need the
    wire-format JSON (CLI stdout, wrapper JSON-RPC) call ``.to_dict()`` at
    their serialization boundary.
    """
    repo_path = generator.repo_location
    output_dir = generator.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    initialize_codeboardingignore(output_dir)

    resolved_target_ref = target_ref or ""

    existing = load_full_analysis(output_dir)
    if existing is None:
        return FullAnalysisRequiredPayload(
            message="No existing analysis.json; full analysis required.",
            base_ref=base_ref or "",
            target_ref=resolved_target_ref,
        )

    resolved_base_ref = base_ref or last_successful_commit(output_dir)
    if not resolved_base_ref:
        return FullAnalysisRequiredPayload(
            message="No prior incremental metadata found; full analysis required before running incremental.",
            target_ref=resolved_target_ref,
        )

    target_ref_error = _validate_target_ref(repo_path, resolved_target_ref)
    if target_ref_error is not None:
        return FullAnalysisRequiredPayload(
            message=target_ref_error, base_ref=resolved_base_ref, target_ref=resolved_target_ref
        )

    parsed_diff = get_parsed_git_diff(repo_path, resolved_base_ref, resolved_target_ref)
    if parsed_diff.error:
        return FullAnalysisRequiredPayload(
            message=f"Git diff failed for incremental analysis: {parsed_diff.error}",
            base_ref=resolved_base_ref,
            target_ref=resolved_target_ref,
        )

    changes = detect_changes_from_parsed_diff(parsed_diff)
    if any(c.change_type in (ChangeType.RENAMED, ChangeType.COPIED) for c in changes.changes):
        return FullAnalysisRequiredPayload(
            message="Rename/copy changes detected; full analysis required until rename handling is implemented.",
            base_ref=resolved_base_ref,
            target_ref=resolved_target_ref,
        )
    analysis_path = output_dir / "analysis.json"
    if changes.is_empty():
        source_identity = _resolve_source_identity(repo_path, resolved_target_ref)
        write_last_run_metadata(
            output_dir,
            repo_path,
            mode="incremental",
            analysis_path=analysis_path,
            source_identity=source_identity,
            diff_base_ref=_diff_base_for_successful_target(repo_path, resolved_target_ref, source_identity),
        )
        return NoChangesPayload(
            base_ref=resolved_base_ref,
            target_ref=resolved_target_ref,
            resolved_target_commit=source_identity,
            change_set=changes,
            metadata_path=output_dir / "incremental_run_metadata.json",
            analysis_path=analysis_path,
        )

    root_analysis, _ = existing

    with contextlib.redirect_stdout(io.StringIO()):
        generator.pre_analysis()

    if generator.static_analysis is None:
        return FullAnalysisRequiredPayload(
            message="Static analysis could not be initialized; full analysis required.",
            base_ref=resolved_base_ref,
            target_ref=resolved_target_ref,
        )

    symbol_resolver = StaticAnalysisSymbolResolver(generator.static_analysis, repo_path)

    updater = IncrementalUpdater(
        root_analysis,
        symbol_resolver=symbol_resolver,
        parsed_diff=parsed_diff,
    )
    delta = updater.compute_delta(
        added_files=changes.added_files,
        modified_files=changes.modified_files,
        deleted_files=changes.deleted_files,
        changes=changes,
    )

    with contextlib.redirect_stdout(io.StringIO()):
        incremental_result = generator.generate_analysis_incremental(
            delta=delta,
            base_ref=resolved_base_ref,
            parsed_diff=parsed_diff,
        )

    source_identity = _resolve_source_identity(repo_path, resolved_target_ref)
    metadata_path: Path | None = None
    if not incremental_result.summary.requires_full_analysis and incremental_result.analysis_path is not None:
        write_last_run_metadata(
            output_dir,
            repo_path,
            mode="incremental",
            analysis_path=incremental_result.analysis_path,
            source_identity=source_identity,
            diff_base_ref=_diff_base_for_successful_target(repo_path, resolved_target_ref, source_identity),
        )
        metadata_path = output_dir / "incremental_run_metadata.json"

    return IncrementalCompletedPayload(
        result=incremental_result,
        base_ref=resolved_base_ref,
        target_ref=resolved_target_ref,
        resolved_target_commit=source_identity,
        change_set=changes,
        incremental_delta=delta,
        metadata_path=metadata_path,
    )
