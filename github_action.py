import logging
import os
import shutil
from pathlib import Path

from codeboarding_workflows.analysis import BaselineUnavailableError, run_full, run_incremental
from codeboarding_workflows.rendering import render_docs
from diagram_analysis import RunContext, RunPaths
from repo_utils import checkout_repo, clone_repository
from utils import ANALYSIS_FILENAME, CODEBOARDING_DIR_NAME, FINGERPRINT_FILENAME, create_temp_repo_folder

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
    """Copy existing analysis files into the temp folder so incremental analysis can use them.

    ``fingerprint.json`` is the whole-tree baseline the git-free change detection
    diffs against; without it the incremental run raises ``BaselineUnavailableError``
    and falls back to a full analysis.
    """
    for filename in (ANALYSIS_FILENAME, FINGERPRINT_FILENAME, "analysis_manifest.json"):
        src = existing_analysis_dir / filename
        if src.is_file():
            shutil.copy2(src, temp_repo_folder / filename)
            logger.info(f"Seeded existing {filename} for incremental analysis")


def _run_analysis(run_paths: RunPaths, run_context: RunContext) -> Path:
    """Incremental when a usable baseline was seeded, else full.

    ``run_incremental`` detects the changed files itself (git-free, by diffing the
    seeded ``fingerprint.json`` against the checkout) and reuses the baseline's
    depth. When no usable baseline exists it raises ``BaselineUnavailableError``;
    the Action can't prompt a user, so it falls back to a full run rather than
    failing the docs build.
    """
    try:
        return run_incremental(run_paths, run_context)
    except BaselineUnavailableError as exc:
        logger.info("No usable baseline (%s); running full analysis.", exc)
        return run_full(run_paths, run_context, depth_level=int(os.getenv("DIAGRAM_DEPTH_LEVEL", "1")))


def generate_analysis(
    repo_url: str,
    source_branch: str,
    target_branch: str,
    extension: str,
    output_dir: str = CODEBOARDING_DIR_NAME,
    existing_analysis_dir: str | None = None,
) -> Path:
    """Generate analysis for a GitHub repository URL (GitHub Action entry point)."""
    os.environ.setdefault("CODEBOARDING_SOURCE", "github_action")
    repo_root = Path(os.getenv("REPO_ROOT", "repos"))
    repo_name = clone_repository(repo_url, repo_root)
    repo_dir = repo_root / repo_name
    run_context = RunContext.resolve(repo_dir=repo_dir, project_name=repo_name)
    checkout_repo(repo_dir, source_branch)
    temp_repo_folder = create_temp_repo_folder()

    if existing_analysis_dir:
        _seed_existing_analysis(Path(existing_analysis_dir), temp_repo_folder)

    run_paths = RunPaths(repo_path=repo_dir, output_dir=temp_repo_folder, project_name=repo_name)
    analysis_path = _run_analysis(run_paths, run_context)

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
