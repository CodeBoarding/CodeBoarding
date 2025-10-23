"""
This module provides functionalities to analyze a git repository and generate
documentation in various formats like Markdown, HTML, MDX, and reStructuredText.

It is designed to be used in a CI/CD pipeline, such as a GitHub Action, to
automate the generation of codebase documentation.
"""

import logging
import os
from pathlib import Path
from typing import List, Callable, Dict, Any

from dotenv import load_dotenv

from agents.agent_responses import AnalysisInsights
from diagram_analysis import DiagramGenerator
from output_generators.markdown import generate_markdown_file
from output_generators.html import generate_html_file
from output_generators.mdx import generate_mdx_file
from output_generators.sphinx import generate_rst_file
from repo_utils import clone_repository, checkout_repo
from utils import create_temp_repo_folder

logger = logging.getLogger(__name__)

# Map file extensions to their corresponding generation function and configuration
GENERATOR_MAP: Dict[str, Dict[str, Any]] = {
    ".md": {"func": generate_markdown_file, "use_output_dir_in_ref": True},
    ".html": {"func": generate_html_file, "use_output_dir_in_ref": False},
    ".mdx": {"func": generate_mdx_file, "use_output_dir_in_ref": True},
    ".rst": {"func": generate_rst_file, "use_output_dir_in_ref": True},
}


def _process_and_generate_docs(
    generation_func: Callable,
    analysis_files: List[str],
    repo_name: str,
    repo_url: str,
    target_branch: str,
    temp_repo_folder: Path,
    output_dir: str,
    use_output_dir_in_ref: bool = True,
):
    """
    A helper function that iterates through analysis files, processes them,
    and calls the appropriate generation function to create documentation.

    Args:
        generation_func (Callable): The function to call for generating the specific doc format
                                    (e.g., generate_markdown_file).
        analysis_files (List[str]): A list of paths to the JSON analysis files.
        repo_name (str): The name of the repository.
        repo_url (str): The URL of the repository.
        target_branch (str): The branch of the repository to link to.
        temp_repo_folder (Path): The temporary directory where the repository is cloned.
        output_dir (str): The directory where the output files will be stored.
        use_output_dir_in_ref (bool): Flag to include the output directory in the repository URL reference.
    """
    for file in analysis_files:
        # Skip non-JSON files or the version file
        if not str(file).endswith(".json") or "codeboarding_version.json" in str(file):
            continue

        print(f"Processing analysis file: {file}")
        with open(file, 'r') as f:
            analysis = AnalysisInsights.model_validate_json(f.read())
            logger.info(f"Generated analysis from file: {file}")

        # Standardize the output filename for the main analysis file
        fname = Path(file).stem
        if fname.endswith("analysis"):
            fname = "overview"

        # Construct the base repository URL for source code links
        repo_ref = f"{repo_url}/blob/{target_branch}"
        if use_output_dir_in_ref:
            repo_ref += f"/{output_dir}"

        # Call the specific output generator function
        generation_func(
            fname=fname,
            analysis=analysis,
            repo_name=repo_name,
            repo_ref=repo_ref,
            linked_files=analysis_files,
            temp_dir=temp_repo_folder,
        )


def generate_analysis(repo_url: str, source_branch: str, target_branch: str, extension: str,
                      output_dir: str = ".codeboarding") -> Path:
    """
    Clones a repository, generates a code analysis, and produces documentation
    in the specified format.

    This is the main orchestrator function intended to be used in a CI/CD context.

    Args:
        repo_url (str): The URL of the GitHub repository to analyze.
        source_branch (str): The source branch to check out for analysis.
        target_branch (str): The branch to use in documentation links.
        extension (str): The file extension of the desired output format
                         (e.g., ".md", ".html").
        output_dir (str, optional): The directory to save generated files.
                                    Defaults to ".codeboarding".

    Returns:
        Path: The path to the temporary folder containing the generated documentation.

    Raises:
        ValueError: If the provided extension is not supported.
    """
    repo_root = Path(os.environ.get("REPO_ROOT", "."))
    repo_name = clone_repository(repo_url, repo_root)
    repo_dir = repo_root / repo_name
    checkout_repo(repo_dir, source_branch)
    temp_repo_folder = create_temp_repo_folder()

    generator = DiagramGenerator(repo_location=repo_dir,
                                 temp_folder=temp_repo_folder,
                                 repo_name=repo_name,
                                 output_dir=temp_repo_folder,
                                 depth_level=int(os.environ.get("DIAGRAM_DEPTH_LEVEL", 2)))

    analysis_files = generator.generate_analysis()

    # Check if the requested extension is supported
    if extension not in GENERATOR_MAP:
        raise ValueError(f"Unsupported extension: {extension}. Supported formats are: {list(GENERATOR_MAP.keys())}")

    # Get the configuration for the specified extension
    config = GENERATOR_MAP[extension]
    
    # Process the analysis files and generate the documentation
    _process_and_generate_docs(
        generation_func=config["func"],
        analysis_files=analysis_files,
        repo_name=repo_name,
        repo_url=repo_url,
        target_branch=target_branch,
        temp_repo_folder=temp_repo_folder,
        output_dir=output_dir,
        use_output_dir_in_ref=config["use_output_dir_in_ref"],
    )

    return temp_repo_folder
