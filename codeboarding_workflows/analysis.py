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

from diagram_analysis import DiagramGenerator
from diagram_analysis.io_utils import load_analysis_metadata, load_full_analysis
from diagram_analysis.run_context import DEFAULT_DEPTH_LEVEL, RunContext, RunPaths
from repo_utils.fingerprint_diff import BaselineUnavailableError, detect_changes_from_fingerprint
from telemetry.events import track_analysis

logger = logging.getLogger(__name__)

__all__ = ["BaselineUnavailableError", "run_full", "run_partial", "run_incremental", "run_incremental_workflow"]


def build_generator(
    run_paths: RunPaths,
    run_context: RunContext,
    depth_level: int,
    monitoring_enabled: bool = False,
    static_analyzer=None,
    changes=None,
) -> DiagramGenerator:
    return DiagramGenerator(
        repo_location=run_paths.repo_path,
        temp_folder=run_paths.output_dir,
        repo_name=run_paths.project_name,
        output_dir=run_paths.output_dir,
        depth_level=depth_level,
        run_id=run_context.run_id,
        log_path=run_context.log_path,
        monitoring_enabled=monitoring_enabled,
        static_analyzer=static_analyzer,
        changes=changes,
    )


def run_full(
    run_paths: RunPaths,
    run_context: RunContext,
    depth_level: int = DEFAULT_DEPTH_LEVEL,
    monitoring_enabled: bool = False,
    force_full: bool = False,
    static_analyzer=None,
    source_sha: str | None = None,
) -> Path:
    """Full analysis scope — rebuild the whole diagram from scratch.

    ``source_sha`` is forwarded to ``StaticAnalyzer.analyze`` so the on-disk
    static-analysis run artifact (sibling of ``analysis.json``) gets a
    matching SHA tag — enabling the next run's SHA-gated cache reuse.
    """
    logger.info(f"Running FULL analysis workflow for repo '{run_paths.project_name}'.")
    generator = build_generator(
        run_paths,
        run_context,
        depth_level=depth_level,
        monitoring_enabled=monitoring_enabled,
        static_analyzer=static_analyzer,
    )
    generator.force_full_analysis = force_full
    generator.source_sha = source_sha
    return generator.generate_analysis()


def run_partial(
    run_paths: RunPaths,
    run_context: RunContext,
    component_id: str,
) -> None:
    """Partial scope — regenerate a single component within an existing analysis.

    Raises ``BaselineUnavailableError`` when no ``analysis.json`` baseline
    exists — partial updates a *component within* an existing analysis and
    has no meaningful behavior without one.
    """
    logger.info(
        f"Running PARTIAL analysis workflow for project '{run_paths.project_name}', component '{component_id}'."
    )

    # Depth is the baseline's configured cap (metadata.depth_cap), with a
    # legacy depth_level fallback — not the realized depth.
    metadata = load_analysis_metadata(run_paths.output_dir)
    if metadata is None:
        raise BaselineUnavailableError(
            f"No baseline analysis.json found in '{run_paths.output_dir}'. Run a full analysis first."
        )

    depth_level = int(metadata.get("depth_cap", metadata.get("depth_level", DEFAULT_DEPTH_LEVEL)))
    generator = build_generator(run_paths, run_context, depth_level=depth_level)
    generator.pre_analysis()

    full_analysis = load_full_analysis(run_paths.output_dir)
    if full_analysis is None:
        # Metadata was present but the unified read failed — treat as a
        # corrupt or partially-written baseline, same surfacing as cold start.
        raise BaselineUnavailableError(
            f"analysis.json in '{run_paths.output_dir}' could not be parsed as a unified analysis."
        )

    root_analysis, sub_analyses = full_analysis

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

    _, sub_analysis, _ = generator.process_component(component_to_analyze)
    if sub_analysis is None:
        logger.error(f"Failed to generate sub-analysis for component '{component_id}'")
        return

    # Add the sub-analysis to the in-memory tree so global relations rebuild with
    # its subcomponents. Rebuild reads each sub's LLM relation labels, which live
    # only in memory (they aren't serialized), so it must run before the save.
    sub_analyses[component_id] = sub_analysis
    # The baseline we loaded may predate child scopes being confined to their parent.
    # Repair it here too: this flow only regenerates one component, so nothing else
    # would reconcile the rest of the tree before the save asserts containment.
    generator._rescope_child_analyses(root_analysis, sub_analyses, set())
    # persist_side_artifacts=False: an expansion must not rewrite file_coverage.json
    # or the static-analysis cache. The latter would drop the static_analysis.sha
    # tag (no source_sha here) and force the next incremental run to cold-start.
    generator.finalize_and_save(root_analysis, sub_analyses, persist_side_artifacts=False)
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

    Raises ``BaselineUnavailableError`` when no baseline analysis exists — callers
    should surface a "run full analysis" prompt rather than silently degrading to
    an unscoped run.
    """
    # Depth comes from the existing analysis.json's configured cap
    # (metadata.depth_cap, falling back to depth_level for legacy baselines
    # that predate it) — not the realized depth_level, so a run that stopped
    # short of its cap doesn't leave incremental permanently capped shallower
    # than what was actually configured. Fail fast on cold-start:
    # ``_generate_subcomponents`` requires the prior depth to re-detail
    # changed components.
    metadata = load_analysis_metadata(run_paths.output_dir)
    if metadata is None:
        raise BaselineUnavailableError(
            f"No baseline analysis.json found in '{run_paths.output_dir}'. Run a full analysis first."
        )
    depth_level = int(metadata.get("depth_cap", metadata.get("depth_level", DEFAULT_DEPTH_LEVEL)))

    changes = detect_changes_from_fingerprint(run_paths.repo_path, run_paths.output_dir)
    logger.info(
        f"Running INCREMENTAL analysis workflow for project '{run_paths.project_name}' "
        f"({len(changes.files)} changed file(s))."
    )

    generator = build_generator(
        run_paths,
        run_context,
        depth_level=depth_level,
        monitoring_enabled=monitoring_enabled,
        static_analyzer=static_analyzer,
        changes=changes,
    )
    return run_incremental_workflow(generator)


def run_incremental_workflow(generator: DiagramGenerator) -> Path:
    """Run incremental analysis when a baseline exists, otherwise fall back to a full run.

    Public kernel used by ``github_action.py``, the desktop wrapper, and
    ``run_incremental`` (CLI). Shape:
    1. If no prior ``analysis.json`` is present, run full analysis.
    2. Otherwise hand the loaded baseline to ``generate_analysis_incremental``,
       which itself falls back to a full run when the cluster snapshot is
       missing or the cluster delta produces nothing actionable.
    """
    output_dir = generator.output_dir
    existing = load_full_analysis(output_dir)
    metadata = load_analysis_metadata(output_dir)
    if existing is None or metadata is None:
        logger.info("No existing analysis baseline; running full analysis.")
        return generator.generate_analysis()

    root_analysis, sub_analyses = existing

    if not root_analysis.components:
        logger.info("Baseline analysis has no components; running full analysis.")
        return generator.generate_analysis()

    return generator.generate_analysis_incremental(root_analysis, sub_analyses)
