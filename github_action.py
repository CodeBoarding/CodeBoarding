import json
import logging
import os
import shutil
from pathlib import Path

from agents.agent_responses import AnalysisInsights
from diagram_analysis import DiagramGenerator
from diagram_analysis.analysis_json import build_id_to_name_map, parse_unified_analysis
from output_generators.html import generate_html_file
from output_generators.markdown import generate_markdown_file
from output_generators.mdx import generate_mdx_file
from output_generators.sphinx import generate_rst_file
from repo_utils import checkout_repo, clone_repository
from utils import create_temp_repo_folder, sanitize

logger = logging.getLogger(__name__)


def _load_all_analyses(analysis_path: Path) -> list[tuple[str, AnalysisInsights, set[str]]]:
    """Load the unified analysis.json and return a list of (file_name, analysis, expanded_components) tuples.

    Returns the root analysis as 'overview' plus one entry per expanded component.
    """
    with open(analysis_path, "r") as f:
        data = json.load(f)

    root_analysis, sub_analyses = parse_unified_analysis(data)

    # Build a complete id-to-name mapping across all levels
    id_to_name = build_id_to_name_map(root_analysis, sub_analyses)

    # Root analysis: expanded components are those that have sub-analyses
    root_expanded = set(sub_analyses.keys())
    entries: list[tuple[str, AnalysisInsights, set[str]]] = [("overview", root_analysis, root_expanded)]

    # Sub-analyses: determine which of their components are further expanded
    for comp_id, sub_analysis in sub_analyses.items():
        sub_expanded = {c.component_id for c in sub_analysis.components if c.component_id in sub_analyses}
        comp_name = id_to_name.get(comp_id, comp_id)
        fname = sanitize(comp_name)
        entries.append((fname, sub_analysis, sub_expanded))

    return entries


def generate_markdown(
    analysis_path: Path, repo_name: str, repo_url: str, target_branch: str, temp_repo_folder: Path, output_dir: str
):
    entries = _load_all_analyses(analysis_path)
    for fname, analysis, expanded_components in entries:
        logger.info(f"Generating markdown for: {fname}")
        generate_markdown_file(
            fname,
            analysis,
            repo_name,
            repo_ref=f"{repo_url}/blob/{target_branch}/{output_dir}",
            expanded_components=expanded_components,
            temp_dir=temp_repo_folder,
        )


def generate_html(analysis_path: Path, repo_name: str, repo_url: str, target_branch: str, temp_repo_folder: Path):
    entries = _load_all_analyses(analysis_path)
    for fname, analysis, expanded_components in entries:
        logger.info(f"Generating HTML for: {fname}")
        generate_html_file(
            fname,
            analysis,
            repo_name,
            repo_ref=f"{repo_url}/blob/{target_branch}",
            expanded_components=expanded_components,
            temp_dir=temp_repo_folder,
        )


def generate_mdx(
    analysis_path: Path, repo_name: str, repo_url: str, target_branch: str, temp_repo_folder: Path, output_dir: str
):
    entries = _load_all_analyses(analysis_path)
    for fname, analysis, expanded_components in entries:
        logger.info(f"Generating MDX for: {fname}")
        generate_mdx_file(
            fname,
            analysis,
            repo_name,
            repo_ref=f"{repo_url}/blob/{target_branch}/{output_dir}",
            expanded_components=expanded_components,
            temp_dir=temp_repo_folder,
        )


def generate_rst(
    analysis_path: Path, repo_name: str, repo_url: str, target_branch: str, temp_repo_folder: Path, output_dir: str
):
    entries = _load_all_analyses(analysis_path)
    for fname, analysis, expanded_components in entries:
        logger.info(f"Generating RST for: {fname}")
        generate_rst_file(
            fname,
            analysis,
            repo_name,
            repo_ref=f"{repo_url}/blob/{target_branch}/{output_dir}",
            expanded_components=expanded_components,
            temp_dir=temp_repo_folder,
        )


def _seed_existing_analysis(existing_analysis_dir: Path, temp_repo_folder: Path) -> None:
    """Copy existing analysis files into the temp folder so incremental analysis can use them."""
    for filename in ("analysis.json", "analysis_manifest.json"):
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
):
    """
    Generate analysis for a GitHub repository URL.
    This function is intended to be used in a GitHub Action context.

    Args:
        existing_analysis_dir: Path to a directory containing a previous analysis.json
            and analysis_manifest.json. When provided, incremental analysis is attempted
            before falling back to a full analysis.
    """
    repo_root = Path(os.getenv("REPO_ROOT", "repos"))
    repo_name = clone_repository(repo_url, repo_root)
    repo_dir = repo_root / repo_name
    checkout_repo(repo_dir, source_branch)
    temp_repo_folder = create_temp_repo_folder()

    # Seed previous analysis files so incremental update can detect changes
    if existing_analysis_dir:
        _seed_existing_analysis(Path(existing_analysis_dir), temp_repo_folder)

    generator = DiagramGenerator(
        repo_location=repo_dir,
        temp_folder=temp_repo_folder,
        repo_name=repo_name,
        output_dir=temp_repo_folder,
        depth_level=int(os.getenv("DIAGRAM_DEPTH_LEVEL", "1")),
    )

    # Use smart analysis: tries incremental first, falls back to full
    analysis_files = generator.generate_analysis_smart()

    # The generator now returns a single analysis.json path
    analysis_path = Path(analysis_files[0])

    # Now generate the output docs:
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
