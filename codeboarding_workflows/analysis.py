"""Scope-level analysis workflows.

Three scopes, one shared generator builder. Each function takes a local
repo path and the minimum context needed to run; they are source-agnostic
(see ``codeboarding_workflows.sources`` for local/remote materialization).
"""

import logging
from pathlib import Path

from codeboarding_workflows.incremental import run_incremental_workflow
from diagram_analysis import DiagramGenerator
from diagram_analysis.io_utils import load_full_analysis, save_sub_analysis
from diagram_analysis.run_metadata import last_successful_commit, write_full_run_metadata
from repo_utils.diff_parser import detect_changes

logger = logging.getLogger(__name__)


def _build_generator(
    repo_name: str,
    repo_path: Path,
    output_dir: Path,
    run_id: str,
    log_path: str,
    depth_level: int = 1,
    monitoring_enabled: bool = False,
    static_analyzer=None,
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
    generator = _build_generator(
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
    generator = _build_generator(
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
    depth_level: int = 1,
    monitoring_enabled: bool = False,
    static_analyzer=None,
    base_ref: str | None = None,
    target_ref: str | None = None,
    source_sha: str | None = None,
) -> Path:
    """Incremental scope — cluster-driven update of an existing ``analysis.json``.

    Resolves the diff baseline and computes a ``ChangeSet`` to scope the
    cluster delta. ``base_ref`` defaults to the last successful commit
    recorded in metadata; ``target_ref`` defaults to ``""`` (working tree
    plus untracked). Falls back to unscoped (no drift filtering) when no
    baseline is available or the diff fails.

    Returns the path to the (possibly updated) analysis. When no baseline or
    cluster snapshot exists, falls back to a full run via
    ``run_incremental_workflow``.
    """
    generator = _build_generator(
        repo_name=project_name,
        repo_path=repo_path,
        output_dir=output_dir,
        run_id=run_id,
        log_path=log_path,
        depth_level=depth_level,
        monitoring_enabled=monitoring_enabled,
        static_analyzer=static_analyzer,
    )
    generator.source_sha = source_sha

    effective_base = base_ref if base_ref is not None else last_successful_commit(output_dir)
    if effective_base is None:
        logger.info("No baseline ref available; running unscoped incremental.")
        generator.changes = None
    else:
        changes = detect_changes(repo_path, effective_base, target_ref or "")
        if changes.error:
            logger.warning("detect_changes failed (%s); running unscoped incremental.", changes.error)
            generator.changes = None
        else:
            generator.changes = changes

    return run_incremental_workflow(generator)
