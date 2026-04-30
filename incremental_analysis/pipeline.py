"""Library-layer incremental pipeline.

Owns the incremental algorithm itself: load prior analysis, detect changes,
classify per-method statuses, apply the delta, run the semantic trace,
patch impacted scopes, save, and write run metadata.

Callers hand it an :class:`IncrementalInputs` value — the typed contract
that replaces the old ``DiagramGenerator``-as-grab-bag dependency. The
workflow layer (``codeboarding_workflows``) is the only place that knows
how to bind these inputs to a generator instance.
"""

import contextlib
import io
import logging
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from agents.llm_config import MONITORING_CALLBACK, initialize_llms
from analysis_artifact.schema import FileCoverageSummary
from analysis_artifact.store import load_full_analysis, save_analysis
from incremental_analysis.delta_application import (
    apply_method_delta,
    drop_deltas_for_pruned_components,
    prune_empty_components,
)
from incremental_analysis.models import (
    IncrementalRunResult,
    IncrementalSummary,
    IncrementalSummaryKind,
)
from incremental_analysis.payload import (
    IncrementalCompletedPayload,
    IncrementalRunPayload,
    NoChangesPayload,
    RequiresFullAnalysisPayload,
)
from incremental_analysis.tracer import run_trace
from incremental_analysis.updater import IncrementalUpdater
from diagram_analysis.run_metadata import METADATA_FILENAME, write_incremental_run_metadata
from incremental_analysis.scope_planner import (
    apply_patch_scopes,
    build_ownership_index,
    derive_patch_scopes,
    normalize_changes_for_delta,
)
from incremental_analysis.symbol_resolver import StaticAnalysisSymbolResolver
from repo_utils import get_git_commit_hash, get_repo_state_hash
from repo_utils.diff_parser import detect_changes
from repo_utils.git_ops import git_object_type, resolve_ref, worktree_has_changes
from repo_utils.ignore import initialize_codeboardingignore
from static_analyzer.analysis_result import StaticAnalysisResults
from utils import ANALYSIS_FILENAME, CODEBOARDING_DIR_NAME

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class IncrementalInputs:
    """Narrow contract the incremental pipeline consumes.

    The pipeline does not know about ``DiagramGenerator``; the workflow
    layer binds the three callables to whichever generator (or test
    fixture) is appropriate. ``prepare_static_analysis`` is invoked only
    after the cheap pre-flight checks pass — keeping early aborts free.
    """

    repo_path: Path
    output_dir: Path
    repo_name: str
    prepare_static_analysis: Callable[[], StaticAnalysisResults | None]
    build_file_coverage_summary: Callable[[], FileCoverageSummary | None]
    write_file_coverage: Callable[[], None]


def _resolve_source_identity(repo_dir: Path, ref: str | None) -> str:
    """Return a stable identifier for the source tree being analyzed.

    Why: a dirty worktree has no commit hash, so we fall back to a content
    hash (``get_repo_state_hash``) so later incremental runs can still match.
    When *ref* is given, we resolve it to its commit SHA — or pass a tree-ish
    ref through unchanged, since a tree SHA is already a stable identity.
    """
    if not ref:
        return (
            get_repo_state_hash(repo_dir)
            if worktree_has_changes(repo_dir, exclude_patterns=(CODEBOARDING_DIR_NAME,))
            else get_git_commit_hash(repo_dir)
        )

    if git_object_type(repo_dir, ref) == "tree":
        return resolve_ref(repo_dir, ref) or ref

    resolved = resolve_ref(repo_dir, ref)
    if resolved is None:
        logger.warning("Could not resolve source ref '%s'; preserving original value", ref)
        return ref
    return resolved


def _diff_base_for_successful_target(repo_dir: Path, target_ref: str | None, source_identity: str) -> str | None:
    if target_ref:
        return source_identity
    return None if worktree_has_changes(repo_dir, exclude_patterns=(CODEBOARDING_DIR_NAME,)) else source_identity


def _target_ref_matches_checkout(repo_dir: Path, target_ref: str) -> bool:
    """Whether ``target_ref`` resolves to the worktree's current HEAD.

    Why: the static analysis and source reads always run against the worktree,
    so if the caller pins a target ref that isn't checked out, the patch would
    be built from the wrong sources.
    """
    head = resolve_ref(repo_dir, "HEAD")
    target = resolve_ref(repo_dir, target_ref)
    if head is None or target is None:
        return False
    return head == target


def _validate_target_ref(repo_path: Path, resolved_target_ref: str) -> str | None:
    """Return an error message if *resolved_target_ref* is incompatible with the current checkout.

    A tree-ish ref is content-addressed and independent of the worktree's
    checkout state, so the HEAD-match and dirty-worktree checks are skipped
    for trees. Wrapper-driven snapshot runs rely on this — they pass a
    pre-staged ``git write-tree`` SHA as the target.
    """
    if not resolved_target_ref:
        return None
    if git_object_type(repo_path, resolved_target_ref) == "tree":
        return None
    if not _target_ref_matches_checkout(repo_path, resolved_target_ref):
        return (
            f"--target-ref {resolved_target_ref!r} does not match the current checkout; "
            "check out the target ref or omit --target-ref."
        )
    if worktree_has_changes(repo_path, exclude_patterns=(CODEBOARDING_DIR_NAME,)):
        return (
            "--target-ref cannot be combined with a dirty worktree; "
            "commit or stash local changes, or omit --target-ref."
        )
    return None


def run_incremental_pipeline(
    inputs: IncrementalInputs,
    base_ref: str,
    target_ref: str,
) -> IncrementalRunPayload:
    """Run a semantic incremental analysis against the typed *inputs*.

    Preconditions: ``inputs.repo_path`` / ``inputs.output_dir`` are absolute
    (caller's ``resolve_local_run_paths``); ``base_ref`` resolves to a
    commit the caller already validated (e.g. from ``last_successful_commit``);
    ``target_ref`` is ``""`` for the worktree or a concrete ref.

    Returns an ``IncrementalRunPayload`` variant. Callers that need the
    wire-format JSON (CLI stdout, wrapper JSON-RPC) call ``.to_dict()`` at
    their serialization boundary.
    """
    repo_path = inputs.repo_path
    output_dir = inputs.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    initialize_codeboardingignore(output_dir)

    def _abort(msg: str) -> RequiresFullAnalysisPayload:
        logger.info("Incremental aborted (base_ref=%s target_ref=%s): %s", base_ref, target_ref or "WORKTREE", msg)
        return RequiresFullAnalysisPayload(message=msg)

    existing = load_full_analysis(output_dir)
    if existing is None:
        return _abort("No existing analysis.json; full analysis required.")

    target_ref_error = _validate_target_ref(repo_path, target_ref)
    if target_ref_error is not None:
        return _abort(target_ref_error)

    change_set = detect_changes(repo_path, base_ref, target_ref)
    if change_set.error:
        return _abort(f"Git diff failed for incremental analysis: {change_set.error}")

    if change_set.has_renames_or_copies():
        return _abort("Rename/copy changes detected; full analysis required until rename handling is implemented.")

    analysis_path = output_dir / ANALYSIS_FILENAME
    if change_set.is_empty():
        source_identity = _resolve_source_identity(repo_path, target_ref)
        write_incremental_run_metadata(
            output_dir,
            repo_path,
            analysis_path=analysis_path,
            source_identity=source_identity,
            diff_base_ref=_diff_base_for_successful_target(repo_path, target_ref, source_identity),
        )
        return NoChangesPayload(
            base_ref=base_ref,
            target_ref=target_ref,
            resolved_target_commit=source_identity,
            change_set=change_set,
            metadata_path=output_dir / METADATA_FILENAME,
            analysis_path=analysis_path,
        )

    root_analysis, sub_analyses = existing

    with contextlib.redirect_stdout(io.StringIO()):
        static_analysis = inputs.prepare_static_analysis()

    if static_analysis is None:
        return _abort("Static analysis could not be initialized; full analysis required.")

    added_files, modified_files, deleted_files, rename_map = normalize_changes_for_delta(change_set)
    pre_delta_index = build_ownership_index(root_analysis, sub_analyses)
    symbol_resolver = StaticAnalysisSymbolResolver(static_analysis, repo_path)
    updater = IncrementalUpdater(
        analysis=root_analysis,
        symbol_resolver=symbol_resolver,
        repo_dir=repo_path,
        component_resolver=lambda file_path: pre_delta_index.pick_component_for_file(file_path, rename_map),
    )
    delta = updater.compute_delta(
        added_files=added_files,
        modified_files=modified_files,
        deleted_files=deleted_files,
        changes=change_set,
    )

    with contextlib.redirect_stdout(io.StringIO()):
        apply_method_delta(root_analysis, sub_analyses, delta)
        removed_component_ids = prune_empty_components(root_analysis, sub_analyses)
        drop_deltas_for_pruned_components(delta, removed_component_ids)
        post_delta_index = build_ownership_index(root_analysis, sub_analyses)

        agent_llm, parsing_llm = initialize_llms()
        callbacks = [MONITORING_CALLBACK]
        cfgs = {}
        for language in static_analysis.get_languages():
            if not static_analysis.get_source_files(language):
                continue
            try:
                cfgs[language] = static_analysis.get_cfg(language)
            except ValueError:
                # Static-analysis layer raises when a language has source files but
                # no CFG yet (parser bootstrap failures, language disabled). Skip.
                continue
        trace_result = run_trace(
            delta,
            cfgs,
            static_analysis,
            repo_path,
            base_ref,
            parsing_llm,
            parsed_diff=change_set,
            callbacks=callbacks,
        )
        patch_scopes = derive_patch_scopes(
            trace_result,
            root_analysis,
            sub_analyses,
            post_delta_index,
            rename_map,
        )
        if patch_scopes:
            root_analysis, sub_analyses = apply_patch_scopes(
                root_analysis, sub_analyses, patch_scopes, agent_llm, callbacks
            )

        analysis_path = save_analysis(
            analysis=root_analysis,
            output_dir=output_dir,
            sub_analyses=sub_analyses,
            repo_name=inputs.repo_name,
            file_coverage_summary=inputs.build_file_coverage_summary(),
            commit_hash=get_git_commit_hash(repo_path),
        ).resolve()
        inputs.write_file_coverage()

    incremental_result = IncrementalRunResult(
        summary=IncrementalSummary(
            kind=IncrementalSummaryKind.MATERIAL_IMPACT,
            message="Incremental analysis complete.",
            used_llm=True,
        ),
        analysis_path=analysis_path,
    )

    source_identity = _resolve_source_identity(repo_path, target_ref)
    write_incremental_run_metadata(
        output_dir,
        repo_path,
        analysis_path=analysis_path,
        source_identity=source_identity,
        diff_base_ref=_diff_base_for_successful_target(repo_path, target_ref, source_identity),
    )
    metadata_path = output_dir / METADATA_FILENAME

    return IncrementalCompletedPayload(
        result=incremental_result,
        base_ref=base_ref,
        target_ref=target_ref,
        resolved_target_commit=source_identity,
        change_set=change_set,
        incremental_delta=delta,
        metadata_path=metadata_path,
    )
