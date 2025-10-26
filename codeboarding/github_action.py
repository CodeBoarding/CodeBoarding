import logging
import os
from pathlib import Path
from typing import List

from dotenv import load_dotenv

from codeboarding.agents.agent_responses import AnalysisInsights
from codeboarding.diagram_analysis import DiagramGenerator
from codeboarding.output_generators.markdown import generate_markdown_file
from codeboarding.output_generators.html import generate_html_file
from codeboarding.output_generators.mdx import generate_mdx_file
from codeboarding.output_generators.sphinx import generate_rst_file
from codeboarding.repo_utils import clone_repository, checkout_repo
from codeboarding.utils import create_temp_repo_folder

logger = logging.getLogger(__name__)


def generate_markdown(analysis_files: List[str], repo_name: str, repo_url: str, target_branch: str,
                      temp_repo_folder: Path, output_dir):
    for file in analysis_files:
        if str(file).endswith(".json") and "codeboarding_version.json" not in str(file):
            print(f"Processing analysis file: {file}")
            with open(file, 'r') as f:
                analysis = AnalysisInsights.model_validate_json(f.read())
                logger.info(f"Generated analysis file: {file}")
                fname = Path(file).stem
                if fname.endswith("analysis"):
                    fname = "overview"
                generate_markdown_file(fname, analysis, repo_name,
                                       repo_ref=f"{repo_url}/blob/{target_branch}/{output_dir}",
                                       linked_files=analysis_files,
                                       temp_dir=temp_repo_folder)


def generate_html(analysis_files: List[str], repo_name: str, repo_url: str, target_branch: str,
                  temp_repo_folder: Path, output_dir):
    for file in analysis_files:
        if str(file).endswith(".json") and "codeboarding_version.json" not in str(file):
            print(f"Processing analysis file: {file}")
            with open(file, 'r') as f:
                analysis = AnalysisInsights.model_validate_json(f.read())
                logger.info(f"Generated analysis file: {file}")
                fname = Path(file).stem
                if fname.endswith("analysis"):
                    fname = "overview"
                generate_html_file(fname, analysis, repo_name,
                                   repo_ref=f"{repo_url}/blob/{target_branch}",
                                   linked_files=analysis_files,
                                   temp_dir=temp_repo_folder)


def generate_mdx(analysis_files: List[str], repo_name: str, repo_url: str, target_branch: str, temp_repo_folder: Path,
                 output_dir):
    for file in analysis_files:
        if str(file).endswith(".json") and "codeboarding_version.json" not in str(file):
            print(f"Processing analysis file: {file}")
            with open(file, 'r') as f:
                analysis = AnalysisInsights.model_validate_json(f.read())
                logger.info(f"Generated analysis file: {file}")
                fname = Path(file).stem
                if fname.endswith("analysis"):
                    fname = "overview"
                generate_mdx_file(fname, analysis, repo_name,
                                  repo_ref=f"{repo_url}/blob/{target_branch}/{output_dir}",
                                  linked_files=analysis_files,
                                  temp_dir=temp_repo_folder)


def generate_rst(analysis_files: List[str], repo_name: str, repo_url: str, target_branch: str, temp_repo_folder: Path,
                 output_dir):
    for file in analysis_files:
        if str(file).endswith(".json") and "codeboarding_version.json" not in str(file):
            print(f"Processing analysis file: {file}")
            with open(file, 'r') as f:
                analysis = AnalysisInsights.model_validate_json(f.read())
                logger.info(f"Generated analysis file: {file}")
                fname = Path(file).stem
                if fname.endswith("analysis"):
                    fname = "overview"
                generate_rst_file(fname, analysis, repo_name,
                                  repo_ref=f"{repo_url}/blob/{target_branch}/{output_dir}",
                                  linked_files=analysis_files,
                                  temp_dir=temp_repo_folder)


def generate_analysis(repo_url: str, source_branch: str, target_branch: str, extension: str,
                      output_dir: str = ".codeboarding"):
    """
    Generate analysis for a GitHub repository URL.
    This function is intended to be used in a GitHub Action context.
    """
    repo_root = Path(os.environ["REPO_ROOT"])
    repo_name = clone_repository(repo_url, repo_root)
    repo_dir = repo_root / repo_name
    checkout_repo(repo_dir, source_branch)
    temp_repo_folder = create_temp_repo_folder()

    generator = DiagramGenerator(repo_location=repo_dir,
                                 temp_folder=temp_repo_folder,
                                 repo_name=repo_name,
                                 output_dir=temp_repo_folder,
                                 depth_level=int(os.environ["DIAGRAM_DEPTH_LEVEL"]))

    analysis_files = generator.generate_analysis()

    # Now generated the markdowns:
    match extension:
        case ".md":
            generate_markdown(analysis_files, repo_name, repo_url,
                              target_branch, temp_repo_folder, output_dir)
        case ".html":
            generate_html(analysis_files, repo_name, repo_url,
                          target_branch, temp_repo_folder, output_dir)
        case ".mdx":
            generate_mdx(analysis_files, repo_name, repo_url,
                         target_branch, temp_repo_folder, output_dir)
        case ".rst":
            generate_rst(analysis_files, repo_name, repo_url,
                         target_branch, temp_repo_folder, output_dir)
        case _:
            raise ValueError(f"Unsupported extension: {extension}")

    return temp_repo_folder
