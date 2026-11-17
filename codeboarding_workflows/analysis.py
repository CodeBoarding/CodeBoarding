"""Scope-level analysis workflows.

Three scopes, one shared generator builder. Each function takes a local
repo path and the minimum context needed to run; they are source-agnostic
(see ``codeboarding_workflows.sources`` for local/remote materialization).

``run_incremental_workflow`` is the kernel shared by the CLI path
(``run_incremental``) and external callers (``github_action.py``, desktop
wrapper) that build their own ``DiagramGenerator`` and skip the local-git
baseline resolution.
"""

import logging
from pathlib import Path

from agents.content_hash import compute_source_tree_hash
from agents.agent_responses import AnalysisInsights
from agents.scope_ids import ROOT_SCOPE_ID
from diagram_analysis import DiagramGenerator
from diagram_analysis.analysis_json import require_format_version
from diagram_analysis.exceptions import UnreadableAnalysisError
from diagram_analysis.io_utils import (
    load_analysis_metadata,
    load_expandable_component_ids,
    load_full_analysis,
    restore_analysis,
    snapshot_analysis,
)
from diagram_analysis.run_context import DEFAULT_DEPTH_CAP, RunContext, RunPaths
from infra.repo_utils.change_detector import ChangeSet
from codeboarding_workflows.fingerprint_diff import (
    BaselineUnavailableError,
    EngineChangedError,
    detect_changes_from_fingerprint,
)
from infra.monitoring.telemetry.events import track_analysis
from static_analyzer.analysis_cache import StaticAnalysisCache

logger = logging.getLogger(__name__)

__all__ = [
    "BaselineUnavailableError",
    "EngineChangedError",
    "run_full",
    "run_partial",
    "run_incremental",
    "run_incremental_workflow",
]


def build_generator(
    run_paths: RunPaths,
    run_context: RunContext,
    depth_cap: int,
    monitoring_enabled: bool = False,
    static_analyzer=None,
    changes=None,
) -> DiagramGenerator:
    return DiagramGenerator(
        repo_location=run_paths.repo_path,
        temp_folder=run_paths.output_dir,
        repo_name=run_paths.project_name,
        output_dir=run_paths.output_dir,
        depth_cap=depth_cap,
        run_id=run_context.run_id,
        log_path=run_context.log_path,
        monitoring_enabled=monitoring_enabled,
        static_analyzer=static_analyzer,
        changes=changes,
    )


def run_full(
    run_paths: RunPaths,
    run_context: RunContext,
    depth_cap: int = DEFAULT_DEPTH_CAP,
    monitoring_enabled: bool = False,
    force_full: bool = False,
    static_analyzer=None,
    source_sha: str | None = None,
) -> Path:
    """Full analysis scope — rebuild the whole diagram from scratch."""
    logger.info(f"Running FULL analysis workflow for repo '{run_paths.project_name}'.")
    generator = build_generator(
        run_paths,
        run_context,
        depth_cap=depth_cap,
        monitoring_enabled=monitoring_enabled,
        static_analyzer=static_analyzer,
    )
    generator.force_full_analysis = force_full
    generator.source_sha = source_sha
    # Progress saves land mid-run, so a failed run must not leave its partial analysis.json behind.
    before = snapshot_analysis(run_paths.output_dir)
    try:
        return generator.generate_analysis()
    except BaseException:
        restore_analysis(run_paths.output_dir, before)
        raise


def run_partial(
    run_paths: RunPaths,
    run_context: RunContext,
    component_id: str,
) -> None:
    """Partial scope — regenerate a single component within an existing analysis.

    Raises ``BaselineUnavailableError`` when no ``analysis.json`` baseline
    this Core reads exists — partial updates a *component within* an existing
    analysis and has no meaningful behavior without one.
    """
    logger.info(
        f"Running PARTIAL analysis workflow for project '{run_paths.project_name}', component '{component_id}'."
    )

    metadata = _baseline_metadata(run_paths.output_dir)
    baseline_source_hash = str(metadata.get("source_tree_hash", ""))
    if not baseline_source_hash:
        raise BaselineUnavailableError(
            "Partial analysis requires a content-versioned source baseline. Run a full analysis first."
        )

    current_source_hash = compute_source_tree_hash(run_paths.repo_path)
    if current_source_hash != baseline_source_hash:
        raise BaselineUnavailableError(
            "Partial analysis requires an up-to-date source baseline. Run incremental analysis before expanding a component."
        )

    depth_cap = int(metadata["depth_cap"])
    root_analysis, sub_analyses = _load_baseline(run_paths.output_dir)
    preserved_expandable_ids = load_expandable_component_ids(run_paths.output_dir)

    component_to_analyze = None
    for component in root_analysis.components:
        if component.component_id == component_id:
            logger.info(f"Updating analysis for component: {component.name}")
            component_to_analyze = component
            break
    if component_to_analyze is None:
        for sub_analysis in sub_analyses.values():
            for component in sub_analysis.components:
                if component.component_id == component_id:
                    logger.info(f"Updating analysis for component: {component.name}")
                    component_to_analyze = component
                    break
            if component_to_analyze is not None:
                break

    if component_to_analyze is None:
        logger.error(f"Component with ID '{component_id}' not found in analysis")
        return

    generator = build_generator(run_paths, run_context, depth_cap=depth_cap, changes=ChangeSet(files=[]))
    generator.source_sha = current_source_hash
    # A component at the persisted cap needs one additional level prepared for expansion.
    generator.prepare_analysis(
        hierarchy_depth=depth_cap + 1,
        target_component=component_to_analyze,
        persisted_scopes={ROOT_SCOPE_ID: root_analysis, **sub_analyses},
    )

    _, sub_analysis, _ = generator.process_component(component_to_analyze)
    if sub_analysis is None:
        logger.error(f"Failed to generate sub-analysis for component '{component_id}'")
        return

    # Add the sub-analysis to the in-memory tree so global relations rebuild with
    # its subcomponents. Rebuild reads each sub's LLM relation labels, which live
    # only in memory (they aren't serialized), so it must run before the save.
    sub_analyses[component_id] = sub_analysis
    # Keep coverage and fingerprint artifacts unchanged; persist only the new lineage below.
    generator.finalize_and_save(
        root_analysis,
        sub_analyses,
        persist_side_artifacts=False,
        preserved_expandable_ids=preserved_expandable_ids,
    )
    generator._persist_static_analysis_artifact()
    logger.info(f"Updated component '{component_id}' in analysis.json")


def run_incremental(
    run_paths: RunPaths,
    run_context: RunContext,
    monitoring_enabled: bool = False,
    static_analyzer=None,
) -> Path:
    """Incremental scope — cluster-driven update of an existing ``analysis.json``.

    Change detection is internal and git-free: fingerprint the repo and diff it
    against the baseline hashes in the existing ``analysis.json``. No caller
    passes a changed-file set or git refs — the CLI and the wrapper both just say
    "update this directory". The source-tree hash doubles as the warm-start tag.

    Raises ``BaselineUnavailableError`` when no baseline analysis this Core reads
    exists, and its subclass ``EngineChangedError`` when another engine version
    wrote the static analysis — callers should surface a "run full analysis"
    prompt rather than silently degrading to an unscoped run.
    """
    metadata = _baseline_metadata(run_paths.output_dir)
    depth_cap = int(metadata["depth_cap"])

    # Why here: the analyzer refuses this pkl too, but only after servers started and a failed run was reported.
    if StaticAnalysisCache(run_paths.output_dir, run_paths.repo_path).from_another_engine():
        raise EngineChangedError(run_paths.output_dir, depth_cap)

    changes = detect_changes_from_fingerprint(run_paths.repo_path, run_paths.output_dir)
    logger.info(
        f"Running INCREMENTAL analysis workflow for project '{run_paths.project_name}' "
        f"({len(changes.files)} changed file(s))."
    )

    generator = build_generator(
        run_paths,
        run_context,
        depth_cap=depth_cap,
        monitoring_enabled=monitoring_enabled,
        static_analyzer=static_analyzer,
        changes=changes,
    )
    return _run_from_baseline(generator, _load_baseline(run_paths.output_dir))


def run_incremental_workflow(generator: DiagramGenerator) -> Path:
    """Run incremental analysis when a baseline exists, otherwise fall back to a full run.

    Public kernel for unattended callers (``github_action.py``): with no prior ``analysis.json``,
    or one this Core cannot read, it runs a full analysis. ``generate_analysis_incremental`` itself
    falls back to a full run when the cluster snapshot is missing or the delta has nothing actionable.
    """
    try:
        baseline = load_full_analysis(Path(generator.output_dir))
    except UnreadableAnalysisError:
        # Why: nobody is there to answer a "run a full analysis" prompt.
        logger.warning("The existing analysis.json cannot be updated; running full analysis.", exc_info=True)
        baseline = None
    return _run_from_baseline(generator, baseline)


def _baseline_metadata(output_dir: Path) -> dict:
    """The baseline's metadata; raises ``BaselineUnavailableError`` unless this Core can update the baseline."""
    try:
        metadata = load_analysis_metadata(output_dir)
        if metadata is not None:
            require_format_version(metadata)
    except UnreadableAnalysisError as error:
        raise BaselineUnavailableError(str(error)) from error
    if metadata is None:
        raise BaselineUnavailableError(f"No baseline analysis.json found in '{output_dir}'. Run a full analysis first.")
    return metadata


def _load_baseline(output_dir: Path) -> tuple[AnalysisInsights, dict[str, AnalysisInsights]]:
    """The root analysis and sub-analyses of a baseline ``_baseline_metadata`` accepted.

    Raises ``BaselineUnavailableError`` when its body is not one this Core can read.
    """
    try:
        baseline = load_full_analysis(output_dir)
    except UnreadableAnalysisError as error:
        raise BaselineUnavailableError(str(error)) from error
    if baseline is None:
        raise BaselineUnavailableError(f"analysis.json in '{output_dir}' was removed during the run.")
    return baseline


def _run_from_baseline(
    generator: DiagramGenerator, baseline: tuple[AnalysisInsights, dict[str, AnalysisInsights]] | None
) -> Path:
    """Update *baseline* incrementally, or run in full without one or with an empty one."""
    output_dir = Path(generator.output_dir)
    # Progress saves land mid-run, so a failed run must not leave its partial analysis.json behind.
    before = snapshot_analysis(output_dir)
    try:
        if baseline is None:
            logger.info("No usable analysis baseline; running full analysis.")
            return generator.generate_analysis()

        root_analysis, sub_analyses = baseline

        if not root_analysis.components:
            logger.info("Baseline analysis has no components; running full analysis.")
            return generator.generate_analysis()

        return generator.generate_analysis_incremental(root_analysis, sub_analyses)
    except BaseException:
        restore_analysis(output_dir, before)
        raise
