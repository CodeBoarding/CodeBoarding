import logging
import os
import shutil
from pathlib import Path

from codeboarding_workflows.incremental import run_incremental_workflow
from codeboarding_workflows.rendering import render_docs
from diagram_analysis import DiagramGenerator, RunContext
from repo_utils import checkout_repo, clone_repository
from utils import ANALYSIS_FILENAME, create_temp_repo_folder

logger = logging.getLogger(__name__)


def generate_markdown(
    analysis_path: Path,
    repo_name: str,
    repo_url: str,
    target_branch: str,
    temp_repo_folder: Path,
    output_dir: str,
) -> None:
    render_docs(
        analysis_path=analysis_path,
        repo_name=repo_name,
        repo_ref=f"{repo_url}/blob/{target_branch}/{output_dir}",
        temp_dir=temp_repo_folder,
        format=".md",
    )


def generate_html(
    analysis_path: Path, repo_name: str, repo_url: str, target_branch: str, temp_repo_folder: Path
) -> None:
    render_docs(
        analysis_path=analysis_path,
        repo_name=repo_name,
        repo_ref=f"{repo_url}/blob/{target_branch}",
        temp_dir=temp_repo_folder,
        format=".html",
    )


def generate_mdx(
    analysis_path: Path,
    repo_name: str,
    repo_url: str,
    target_branch: str,
    temp_repo_folder: Path,
    output_dir: str,
) -> None:
    render_docs(
        analysis_path=analysis_path,
        repo_name=repo_name,
        repo_ref=f"{repo_url}/blob/{target_branch}/{output_dir}",
        temp_dir=temp_repo_folder,
        format=".mdx",
    )


def generate_rst(
    analysis_path: Path,
    repo_name: str,
    repo_url: str,
    target_branch: str,
    temp_repo_folder: Path,
    output_dir: str,
) -> None:
    render_docs(
        analysis_path=analysis_path,
        repo_name=repo_name,
        repo_ref=f"{repo_url}/blob/{target_branch}/{output_dir}",
        temp_dir=temp_repo_folder,
        format=".rst",
    )


def _seed_existing_analysis(existing_analysis_dir: Path, temp_repo_folder: Path) -> None:
    """Copy existing analysis files into the temp folder so incremental analysis can use them."""
    for filename in (ANALYSIS_FILENAME, "analysis_manifest.json"):
        src = existing_analysis_dir / filename
        if src.is_file():
            shutil.copy2(src, temp_repo_folder / filename)
            logger.info(f"Seeded existing {filename} for incremental analysis")


def generate_analysis(
    repo_url: str,
    source_branch: str,
    target_branch: str,
    extension: str,
    output_dir: str = ".codeboarding",
    existing_analysis_dir: str | None = None,
) -> Path:
    """Generate analysis for a GitHub repository URL (GitHub Action entry point)."""
    repo_root = Path(os.getenv("REPO_ROOT", "repos"))
    repo_name = clone_repository(repo_url, repo_root)
    repo_dir = repo_root / repo_name
    run_context = RunContext.resolve(repo_dir=repo_dir, project_name=repo_name)
    checkout_repo(repo_dir, source_branch)
    temp_repo_folder = create_temp_repo_folder()

    if existing_analysis_dir:
        _seed_existing_analysis(Path(existing_analysis_dir), temp_repo_folder)

    generator = DiagramGenerator(
        repo_location=repo_dir,
        temp_folder=temp_repo_folder,
        repo_name=repo_name,
        output_dir=temp_repo_folder,
        depth_level=int(os.getenv("DIAGRAM_DEPTH_LEVEL", "1")),
        run_id=run_context.run_id,
        log_path=run_context.log_path,
    )

    analysis_path = run_incremental_workflow(generator)

    match extension:
        case ".md":
            generate_markdown(analysis_path, repo_name, repo_url, target_branch, temp_repo_folder, output_dir)
        case ".html":
            generate_html(analysis_path, repo_name, repo_url, target_branch, temp_repo_folder)
        case ".mdx":
            generate_mdx(analysis_path, repo_name, repo_url, target_branch, temp_repo_folder, output_dir)
        case ".rst":
            generate_rst(analysis_path, repo_name, repo_url, target_branch, temp_repo_folder, output_dir)
        case _:
            raise ValueError(f"Unsupported extension: {extension}")

    return temp_repo_folder
