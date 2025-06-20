import os
from pathlib import Path

from diagram_analysis import DiagramGenerator
from repo_utils import clone_repository
from utils import create_temp_repo_folder


def generate_full_analysis(diagram_generator: DiagramGenerator):
    analysis_files = diagram_generator.generate_analysis()

    return analysis_files


def generate_analysis(repo_url: str):
    """
    Generate analysis for a GitHub repository URL.
    This function is intended to be used in a GitHub Action context.
    """
    repo_root = Path(os.getenv("REPO_ROOT"))
    repo_name = clone_repository(repo_url, repo_root)
    repo_dir = Path(os.getenv("REPO_ROOT")) / repo_name
    temp_repo_folder = create_temp_repo_folder()

    generator = DiagramGenerator(repo_location=repo_dir,
                                 temp_folder=temp_repo_folder,
                                 repo_name=repo_name,
                                 output_dir=temp_repo_folder,
                                 depth_level=int(os.getenv("DIAGRAM_DEPTH_LEVEL", "2")))
