import json
import logging
import os
import shutil
from pathlib import Path
from typing import Any

import requests

from diagram_analysis import DiagramGenerator, RunContext
from diagram_analysis.analysis_json import build_id_to_name_map, parse_unified_analysis
from diagram_analysis.incremental_pipeline import run_incremental_pipeline
from diagram_analysis.io_utils import load_full_analysis, save_sub_analysis
from diagram_analysis.run_metadata import write_last_run_metadata
from monitoring.paths import generate_log_path
from output_generators.markdown import generate_markdown_file
from repo_utils import clone_repository, get_branch, get_repo_name, upload_onboarding_materials
from utils import create_temp_repo_folder, generate_run_id, remove_temp_repo_folder, sanitize

logger = logging.getLogger(__name__)


def onboarding_materials_exist(project_name: str) -> bool:
    generated_repo_url = f"https://github.com/CodeBoarding/GeneratedOnBoardings/tree/main/{project_name}"
    response = requests.get(generated_repo_url)
    if response.status_code == 200:
        logger.info(f"Repository has already been generated, please check {generated_repo_url}")
        return True
    return False


def generate_analysis(
    repo_name: str,
    repo_path: Path,
    output_dir: Path,
    run_id: str,
    log_path: str,
    depth_level: int = 1,
    monitoring_enabled: bool = False,
    force_full: bool = False,
) -> Path:
    generator = DiagramGenerator(
        repo_location=repo_path,
        temp_folder=output_dir,
        repo_name=repo_name,
        output_dir=output_dir,
        depth_level=depth_level,
        run_id=run_id,
        log_path=log_path,
        monitoring_enabled=monitoring_enabled,
    )
    generator.force_full_analysis = force_full
    analysis_path = generator.generate_analysis()
    write_last_run_metadata(output_dir, repo_path, mode="full", analysis_path=analysis_path)
    return analysis_path


def run_incremental_analysis(
    *,
    repo_path: Path,
    output_dir: Path | None = None,
    project_name: str | None = None,
    depth_level: int = 1,
    base_ref: str | None = None,
    target_ref: str | None = None,
    enable_monitoring: bool = False,
    run_id: str | None = None,
    log_path: str | None = None,
) -> dict[str, Any]:
    """Construct a generator and run the semantic incremental pipeline."""
    repo_path = repo_path.resolve()
    output_dir = (output_dir or (repo_path / ".codeboarding")).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    resolved_project_name = project_name or repo_path.name
    generator = DiagramGenerator(
        repo_location=repo_path,
        temp_folder=output_dir,
        repo_name=resolved_project_name,
        output_dir=output_dir,
        depth_level=depth_level,
        run_id=run_id or generate_run_id(),
        log_path=log_path or generate_log_path(resolved_project_name),
        monitoring_enabled=enable_monitoring,
    )
    return run_incremental_pipeline(generator, base_ref=base_ref, target_ref=target_ref)


def generate_markdown_docs(
    repo_name: str,
    repo_path: Path,
    repo_url: str,
    analysis_path: Path,
    output_dir: Path,
    demo_mode: bool = False,
):
    target_branch = get_branch(repo_path)
    repo_ref = f"{repo_url}/blob/{target_branch}/"

    with open(analysis_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    root_analysis, sub_analyses = parse_unified_analysis(data)

    root_expanded = set(sub_analyses.keys())
    generate_markdown_file(
        "on_boarding",
        root_analysis,
        repo_name,
        repo_ref=repo_ref,
        expanded_components=root_expanded,
        temp_dir=output_dir,
        demo=demo_mode,
    )

    id_to_name = build_id_to_name_map(root_analysis, sub_analyses)
    for comp_id, sub_analysis in sub_analyses.items():
        sub_expanded = {c.component_id for c in sub_analysis.components if c.component_id in sub_analyses}
        comp_name = id_to_name.get(comp_id, comp_id)
        fname = sanitize(comp_name)
        generate_markdown_file(
            fname,
            sub_analysis,
            repo_name,
            repo_ref=repo_ref,
            expanded_components=sub_expanded,
            temp_dir=output_dir,
            demo=demo_mode,
        )


def partial_update(
    repo_path: Path,
    output_dir: Path,
    project_name: str,
    component_id: str,
    run_id: str,
    log_path: str,
    depth_level: int = 1,
):
    """Update a specific component in an existing analysis."""
    generator = DiagramGenerator(
        repo_location=repo_path,
        temp_folder=output_dir,
        repo_name=project_name,
        output_dir=output_dir,
        depth_level=depth_level,
        run_id=run_id,
        log_path=log_path,
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


def generate_docs_remote(
    repo_url: str,
    temp_repo_folder: Path,
    run_id: str,
    log_path: str,
    local_dev: bool = False,
    monitoring_enabled: bool = False,
):
    """Clone a git repo and generate documentation."""
    process_remote_repository(
        repo_url=repo_url,
        output_dir=temp_repo_folder,
        depth_level=int(os.getenv("DIAGRAM_DEPTH_LEVEL", "1")),
        upload=not local_dev,
        cache_check=True,
        run_id=run_id,
        log_path=log_path,
        monitoring_enabled=monitoring_enabled,
    )


def process_remote_repository(
    repo_url: str,
    run_id: str,
    log_path: str,
    output_dir: Path | None = None,
    depth_level: int = 1,
    upload: bool = False,
    cache_check: bool = True,
    monitoring_enabled: bool = False,
):
    """Process a remote repository by cloning and generating documentation."""
    repo_root = Path("repos")
    repo_name = get_repo_name(repo_url)

    if cache_check and onboarding_materials_exist(repo_name):
        logger.info(f"Cache hit for '{repo_name}', skipping documentation generation.")
        return

    repo_name = clone_repository(repo_url, repo_root)
    repo_path = repo_root / repo_name

    temp_folder = create_temp_repo_folder()

    try:
        analysis_path = generate_analysis(
            repo_name=repo_name,
            repo_path=repo_path,
            output_dir=temp_folder,
            depth_level=depth_level,
            run_id=run_id,
            log_path=log_path,
            monitoring_enabled=monitoring_enabled,
        )

        generate_markdown_docs(
            repo_name=repo_name,
            repo_path=repo_path,
            repo_url=repo_url,
            analysis_path=analysis_path,
            output_dir=temp_folder,
            demo_mode=True,
        )

        if output_dir:
            copy_files(temp_folder, output_dir)

        if upload:
            upload_onboarding_materials(repo_name, temp_folder, "results")
    finally:
        RunContext(run_id=run_id, log_path=log_path, repo_dir=repo_path).finalize()
        remove_temp_repo_folder(str(temp_folder))


def process_local_repository(
    repo_path: Path,
    output_dir: Path,
    project_name: str,
    run_id: str,
    log_path: str,
    depth_level: int = 1,
    component_id: str | None = None,
    monitoring_enabled: bool = False,
    incremental: bool = False,
    force_full: bool = False,
):
    if component_id:
        partial_update(
            repo_path=repo_path,
            output_dir=output_dir,
            project_name=project_name,
            component_id=component_id,
            depth_level=depth_level,
            run_id=run_id,
            log_path=log_path,
        )
        return

    if incremental and not force_full:
        result = run_incremental_analysis(
            repo_path=repo_path,
            output_dir=output_dir,
            project_name=project_name,
            depth_level=depth_level,
            run_id=run_id,
            log_path=log_path,
            enable_monitoring=monitoring_enabled,
        )
        logger.info(f"Analysis completed: {result}")
        return

    generate_analysis(
        repo_name=project_name,
        repo_path=repo_path,
        output_dir=output_dir,
        depth_level=depth_level,
        run_id=run_id,
        log_path=log_path,
        monitoring_enabled=monitoring_enabled,
        force_full=force_full,
    )


def copy_files(temp_folder: Path, output_dir: Path):
    """Copy all markdown and JSON files from temp folder to output directory."""
    markdown_files = list(temp_folder.glob("*.md"))
    json_files = list(temp_folder.glob("*.json"))

    all_files = markdown_files + json_files

    if not all_files:
        logger.warning(f"No markdown or JSON files found in {temp_folder}")
        return

    for file in all_files:
        dest_file = output_dir / file.name
        shutil.copy2(file, dest_file)
        logger.info(f"Copied {file.name} to {dest_file}")
