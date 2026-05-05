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
from diagram_analysis.io_utils import load_analysis_metadata, load_full_analysis, save_sub_analysis
from diagram_analysis.run_metadata import write_full_run_metadata
from repo_utils.diff_parser import detect_changes

logger = logging.getLogger(__name__)


class IncrementalUnavailableError(RuntimeError):
    """Raised when incremental analysis cannot run and the caller should fall back to a full run."""


def build_generator(
    repo_name: str,
    repo_path: Path,
    output_dir: Path,
    run_id: str,
    log_path: str,
    depth_level: int = 1,
    monitoring_enabled: bool = False,
    static_analyzer=None,
    changes=None,
) -> DiagramGenerator:
    return DiagramGenerator(
        repo_location=repo_path,
        temp_folder=output_dir,
        repo_name=repo_name,
        output_dir=output_dir,
        depth_level=depth_level,
        run_id=run_id,
        log_path=log_path,
        monitoring_enabled=monitoring_enabled,
        static_analyzer=static_analyzer,
        changes=changes,
    )


def run_full(
    repo_name: str,
    repo_path: Path,
    output_dir: Path,
    run_id: str,
    log_path: str,
    depth_level: int = 1,
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
    generator = build_generator(
        repo_name=repo_name,
        repo_path=repo_path,
        output_dir=output_dir,
        run_id=run_id,
        log_path=log_path,
        depth_level=depth_level,
        monitoring_enabled=monitoring_enabled,
        static_analyzer=static_analyzer,
    )
    generator.force_full_analysis = force_full
    generator.source_sha = source_sha
    analysis_path = generator.generate_analysis()
    write_full_run_metadata(output_dir, repo_path, analysis_path=analysis_path)
    return analysis_path


def run_partial(
    repo_path: Path,
    output_dir: Path,
    project_name: str,
    component_id: str,
    run_id: str,
    log_path: str,
    depth_level: int = 1,
) -> None:
    """Partial scope — regenerate a single component within an existing analysis."""
    generator = build_generator(
        repo_name=project_name,
        repo_path=repo_path,
        output_dir=output_dir,
        run_id=run_id,
        log_path=log_path,
        depth_level=depth_level,
    )
    generator.pre_analysis()

    full_analysis = load_full_analysis(output_dir)
    if full_analysis is None:
        logger.error(f"No analysis.json found in '{output_dir}'. Please ensure the file exists.")
        return

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

    _comp_id, sub_analysis, _new_components = generator.process_component(component_to_analyze)

    if sub_analysis:
        save_sub_analysis(sub_analysis, output_dir, component_id)
        logger.info(f"Updated component '{component_id}' in analysis.json")
    else:
        logger.error(f"Failed to generate sub-analysis for component '{component_id}'")


def run_incremental(
    repo_path: Path,
    output_dir: Path,
    project_name: str,
    run_id: str,
    log_path: str,
    base_ref: str,
    target_ref: str,
    depth_level: int = 1,
    monitoring_enabled: bool = False,
    static_analyzer=None,
    source_sha: str | None = None,
) -> Path:
    """Incremental scope — cluster-driven update of an existing ``analysis.json``.

    ``base_ref`` and ``target_ref`` are required: callers (CLI, wrapper)
    must resolve concrete refs (typically ``last_successful_commit`` and
    the current HEAD) and fail at the boundary if either is unavailable.

    Raises ``IncrementalUnavailableError`` when the diff cannot be computed
    against the given baseline — callers should surface a "run full
    analysis" prompt rather than silently degrading to an unscoped run.
    """
    detected = detect_changes(repo_path, base_ref, target_ref)
    if detected.error:
        raise IncrementalUnavailableError(f"Could not compute diff against baseline {base_ref!r}: {detected.error}")
    changes = detected

    generator = build_generator(
        repo_name=project_name,
        repo_path=repo_path,
        output_dir=output_dir,
        run_id=run_id,
        log_path=log_path,
        depth_level=depth_level,
        monitoring_enabled=monitoring_enabled,
        static_analyzer=static_analyzer,
        changes=changes,
    )
    generator.source_sha = source_sha

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
