import argparse
import logging
import os
import shutil
from pathlib import Path

import requests
from dotenv import load_dotenv
from tqdm import tqdm

from agents.agent_responses import AnalysisInsights
from agents.prompts import initialize_global_factory, PromptType, LLMType
from diagram_analysis import DiagramGenerator
from logging_config import setup_logging
from output_generators.markdown import generate_markdown_file
from repo_utils import clone_repository, get_branch, get_repo_name, store_token, upload_onboarding_materials
from utils import caching_enabled, create_temp_repo_folder, remove_temp_repo_folder, is_monitoring_enabled
from monitoring import monitor_execution

logger = logging.getLogger(__name__)


def validate_env_vars():
    api_provider_keys = [
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "GOOGLE_API_KEY",
        "AWS_BEARER_TOKEN_BEDROCK",
        "OLLAMA_BASE_URL",
        "CEREBRAS_API_KEY",
    ]
    api_env_keys = [(key, os.getenv(key)) for key in api_provider_keys if os.getenv(key) is not None]

    if len(api_env_keys) == 0:
        logger.error(f"API key not set, set one of the following: {api_provider_keys}")
        exit(1)
    elif len(api_env_keys) > 1:
        logger.error(f"Detected multiple API keys set ({api_env_keys}), set ONE of the following: {api_provider_keys}")
        exit(2)

    if not os.getenv("REPO_ROOT"):
        logger.warning("REPO_ROOT environment variable not set, setting REPO_ROOT environment variable to 'repos'")
        os.environ["REPO_ROOT"] = "repos"

    if not os.getenv("ROOT_RESULT"):
        logger.warning(
            "ROOT_RESULT environment variable not set, setting ROOT_RESULT environment variable to 'results'"
        )
        os.environ["ROOT_RESULT"] = "results"


def onboarding_materials_exist(project_name: str, source_dir: str):
    generated_repo_url = f"https://github.com/CodeBoarding/GeneratedOnBoardings/tree/main/{project_name}"
    response = requests.get(generated_repo_url)
    if response.status_code == 200:
        logger.info(f"Repostiory has already been generated, please check {generated_repo_url}")
        return True
    else:
        return False


def generate_docs(repo_name: str, temp_repo_folder: Path, repo_url: str | None = None):
    # Create directories if they don't exist
    repo_root = os.getenv("REPO_ROOT")
    if not repo_root:
        raise ValueError("REPO_ROOT environment variable is not set")
    repos_dir = Path(repo_root)
    repos_dir.mkdir(parents=True, exist_ok=True)

    repo_path = repos_dir / repo_name

    generator = DiagramGenerator(
        repo_location=repo_path,
        temp_folder=temp_repo_folder,
        repo_name=repo_name,
        output_dir=temp_repo_folder,
        depth_level=int(os.getenv("DIAGRAM_DEPTH_LEVEL", "1")),
    )
    analysis_files = generator.generate_analysis()

    for file in analysis_files:
        with open(file, "r") as f:
            analysis = AnalysisInsights.model_validate_json(f.read())
            logger.info(f"Generated analysis file: {file}")
            fname = Path(file).name.split(".json")[0]
            if fname.endswith("analysis"):
                fname = "on_boarding"
            target_branch = get_branch(repo_path)
            generate_markdown_file(
                fname,
                analysis,
                repo_name,
                repo_ref=f"{repo_url}/blob/{target_branch}/",
                linked_files=analysis_files,
                temp_dir=temp_repo_folder,
                demo=True,
            )


def generate_docs_remote(repo_url: str, temp_repo_folder: Path, local_dev=False):
    """
    Clone a git repo to target_dir/<repo-name>.
    Returns the Path to the cloned repository.
    """
    ROOT_RESULT = os.getenv("ROOT_RESULT")
    if not ROOT_RESULT:
        raise ValueError("ROOT_RESULT environment variable is not set")

    REPO_ROOT = os.getenv("REPO_ROOT")
    if not REPO_ROOT:
        raise ValueError("REPO_ROOT environment variable is not set")

    repo_name = get_repo_name(repo_url)
    if not local_dev:
        store_token()
    if caching_enabled() and onboarding_materials_exist(repo_name, ROOT_RESULT):
        logger.info(f"Cache hit for '{repo_name}', skipping documentation generation.")
        return

    repo_name = clone_repository(repo_url, Path(REPO_ROOT))
    generate_docs(repo_name, temp_repo_folder, repo_url)
    if os.path.exists(ROOT_RESULT):
        upload_onboarding_materials(repo_name, temp_repo_folder, ROOT_RESULT)
    else:
        logger.warning(
            f"ROOT_RESULT directory '{ROOT_RESULT}' does not exist. Skipping upload of onboarding materials."
        )


def copy_files(temp_folder: Path, output_dir: Path):
    """Copy all markdown and JSON files from temp folder to output directory."""
    if not output_dir.exists():
        output_dir.mkdir(parents=True, exist_ok=True)
        logger.info(f"Created output directory: {output_dir}")

    # Copy markdown files
    markdown_files = list(temp_folder.glob("*.md"))
    # Copy JSON files
    json_files = list(temp_folder.glob("*.json"))

    all_files = markdown_files + json_files

    if not all_files:
        logger.warning(f"No markdown or JSON files found in {temp_folder}")
        return

    for file in all_files:
        dest_file = output_dir / file.name
        shutil.copy2(file, dest_file)
        logger.info(f"Copied {file.name} to {dest_file}")


if __name__ == "__main__":
    # Initialize the prompt factory for demo.py to use bidirectional prompts
    initialize_global_factory(LLMType.GEMINI_FLASH, PromptType.BIDIRECTIONAL)

    parser = argparse.ArgumentParser(
        description="Generate onboarding documentation for Git repositories",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python demo.py https://github.com/user/repo1
  python demo.py https://github.com/user/repo1 --output-dir ./docs
  python demo.py https://github.com/user/repo1 https://github.com/user/repo2 --output-dir ./output
  python demo.py --help
        """,
    )
    parser.add_argument("repositories", nargs="+", help="One or more Git repository URLs to generate documentation for")
    parser.add_argument("--output-dir", type=Path, help="Directory to copy generated markdown files to")

    args = parser.parse_args()

    load_dotenv()
    validate_env_vars()
    setup_logging()
    logger.info("Starting up…")

    with monitor_execution(run_id="demo_run", enabled=is_monitoring_enabled()) as mon:
        for repo in tqdm(args.repositories, desc="Generating docs for repos"):
            mon.step(f"processing_{get_repo_name(repo)}")

            temp_repo_folder = create_temp_repo_folder()
            try:
                generate_docs_remote(repo, temp_repo_folder, local_dev=True)

                # Copy markdown files to output directory if specified
                if args.output_dir:
                    copy_files(temp_repo_folder, args.output_dir)
            finally:
                remove_temp_repo_folder(temp_repo_folder)
