import logging
import os
from pathlib import Path

from dotenv import load_dotenv

from agents.agent_responses import AnalysisInsights
from diagram_analysis import DiagramGenerator
from logging_config import setup_logging
from markdown_generation import generate_markdown_file
from repo_utils import clone_repository
from utils import create_temp_repo_folder

setup_logging()


def generate_analysis(repo_url: str, target_branch: str):
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

    analysis_files = generator.generate_analysis()

    # Now generated the markdowns:
    for file in analysis_files:
        if file.endswith(".json") and "codeboarding_version.json" not in file:
            with open(file, 'r') as f:
                analysis = AnalysisInsights.model_validate_json(f.read())
                logging.info(f"Generated analysis file: {file}")
                fname = Path(file).name.split(".json")[0]
                if fname.endswith("analysis"):
                    fname = "on_boarding"
                generate_markdown_file(fname, analysis, repo_name,
                                       repo_url=repo_url,
                                       linked_files=analysis_files,
                                       temp_dir=temp_repo_folder)


if __name__ == '__main__':
    load_dotenv()
    generate_analysis("https://github.com/ivanmilevtues/django", "main")
