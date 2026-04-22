import logging
import os
from pathlib import Path

import requests

from codeboarding_workflows.artifact_copy import copy_analysis_artifacts
from codeboarding_workflows.full_analysis import generate_analysis
from codeboarding_workflows.markdown import generate_markdown_docs
from diagram_analysis import RunContext
from repo_utils import clone_repository, get_repo_name, upload_onboarding_materials
from utils import create_temp_repo_folder, remove_temp_repo_folder

logger = logging.getLogger(__name__)


def onboarding_materials_exist(project_name: str) -> bool:
    generated_repo_url = f"https://github.com/CodeBoarding/GeneratedOnBoardings/tree/main/{project_name}"
    try:
        response = requests.get(generated_repo_url, timeout=10)
    except requests.RequestException as exc:
        logger.warning("Cache probe failed for '%s' (%s); proceeding with generation.", project_name, exc)
        return False
    if response.status_code == 200:
        logger.info(f"Repository has already been generated, please check {generated_repo_url}")
        return True
    return False


def generate_docs_remote(
    repo_url: str,
    temp_repo_folder: Path,
    run_id: str,
    log_path: str,
    local_dev: bool = False,
    monitoring_enabled: bool = False,
) -> None:
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
) -> None:
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
            copy_analysis_artifacts(temp_folder, output_dir)

        if upload:
            upload_onboarding_materials(repo_name, temp_folder, "results")
    finally:
        RunContext(run_id=run_id, log_path=log_path, repo_dir=repo_path).finalize()
        remove_temp_repo_folder(str(temp_folder))
