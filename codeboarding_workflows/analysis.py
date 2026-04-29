"""Scope-level analysis workflows.

Three scopes, one shared generator builder. Each function takes a local
repo path and the minimum context needed to run; they are source-agnostic
(see ``codeboarding_workflows.sources`` for local/remote materialization).
"""

import logging
from pathlib import Path

from diagram_analysis import DiagramGenerator
from diagram_analysis.incremental_payload import IncrementalRunPayload
from diagram_analysis.incremental_pipeline import run_incremental_pipeline
from diagram_analysis.io_utils import load_full_analysis, save_sub_analysis
from diagram_analysis.run_metadata import write_full_run_metadata

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
) -> Path:
    """Full analysis scope — rebuild the whole diagram from scratch."""
    generator = _build_generator(
        repo_name=repo_name,
        repo_path=repo_path,
        output_dir=output_dir,
        run_id=run_id,
        log_path=log_path,
        depth_level=depth_level,
        monitoring_enabled=monitoring_enabled,
    )
    generator.force_full_analysis = force_full
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
    base_ref: str,
    target_ref: str,
    depth_level: int = 1,
    monitoring_enabled: bool = False,
    static_analyzer=None,
) -> IncrementalRunPayload:
    """Incremental scope — diff against *base_ref* and propagate only the semantic deltas."""
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
    return run_incremental_pipeline(generator, base_ref=base_ref, target_ref=target_ref)
