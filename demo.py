import argparse
import logging
import os
import shutil
from pathlib import Path

from dotenv import load_dotenv
from git import Repo
from tqdm import tqdm

from agents.agent_responses import AnalysisInsights
from diagram_analysis import DiagramGenerator
from logging_config import setup_logging
from output_generators.markdown import generate_markdown_file
from repo_utils import store_token, clone_repository, upload_onboarding_materials, get_branch
from utils import create_temp_repo_folder, caching_enabled, remove_temp_repo_folder


def onboarding_materials_exist(project_name: str, source_dir: str):
    repo = Repo(source_dir)
    origin = repo.remote(name='origin')
    origin.pull()

    onboarding_repo_path = os.path.join(source_dir, project_name)
    return os.path.isdir(onboarding_repo_path) and len(os.listdir(onboarding_repo_path))


def generate_docs(repo_name: str, temp_repo_folder: Path, repo_url: str = None):
    # Create directories if they don't exist
    repos_dir = Path(os.getenv("REPO_ROOT"))
    ROOT_RESULT = os.getenv("ROOT_RESULT")  # Default path if not set

    repos_dir.mkdir(parents=True, exist_ok=True)

    repo_path = repos_dir / repo_name
    if caching_enabled() and onboarding_materials_exist(repo_name, ROOT_RESULT):
        logging.info(f"Cache hit for '{repo_name}', skipping documentation generation.")
        return

    generator = DiagramGenerator(repo_location=repo_path, temp_folder=temp_repo_folder, repo_name=repo_name,
                                 output_dir=temp_repo_folder, depth_level=int(os.getenv("DIAGRAM_DEPTH_LEVEL", "1")))
    analysis_files = generator.generate_analysis()

    for file in analysis_files:
        with open(file, 'r') as f:
            analysis = AnalysisInsights.model_validate_json(f.read())
            logging.info(f"Generated analysis file: {file}")
            fname = Path(file).name.split(".json")[0]
            if fname.endswith("analysis"):
                fname = "on_boarding"
            target_branch = get_branch(repo_path)
            generate_markdown_file(fname, analysis, repo_name,
                                   repo_ref=f"{repo_url}/blob/{target_branch}/{temp_repo_folder}",
                                   linked_files=analysis_files,
                                   temp_dir=temp_repo_folder, demo=True)


def generate_docs_remote(repo_url: str, temp_repo_folder: Path, local_dev=False) -> str:
    """
    Clone a git repo to target_dir/<repo-name>.
    Returns the Path to the cloned repository.
    """
    if not local_dev:
        store_token()
    repo_name = clone_repository(repo_url, Path(os.getenv("REPO_ROOT")))
    generate_docs(repo_name, temp_repo_folder, repo_url)
    ROOT_RESULT = os.getenv("ROOT_RESULT")  # Default path if not set
    if os.path.exists(ROOT_RESULT):
        upload_onboarding_materials(repo_name, temp_repo_folder, ROOT_RESULT)
    else:
        logging.warning(
            f"ROOT_RESULT directory '{ROOT_RESULT}' does not exist. Skipping upload of onboarding materials.")
    return repo_name


def copy_files(temp_folder: Path, output_dir: Path):
    """Copy all markdown and JSON files from temp folder to output directory."""
    if not output_dir.exists():
        output_dir.mkdir(parents=True, exist_ok=True)
        logging.info(f"Created output directory: {output_dir}")
    
    # Copy markdown files
    markdown_files = list(temp_folder.glob("*.md"))
    # Copy JSON files
    json_files = list(temp_folder.glob("*.json"))
    
    all_files = markdown_files + json_files
    
    if not all_files:
        logging.warning(f"No markdown or JSON files found in {temp_folder}")
        return
    
    for file in all_files:
        dest_file = output_dir / file.name
        shutil.copy2(file, dest_file)
        logging.info(f"Copied {file.name} to {dest_file}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Generate onboarding documentation for Git repositories",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python demo.py https://github.com/user/repo1
  python demo.py https://github.com/user/repo1 --output-dir ./docs
  python demo.py https://github.com/user/repo1 https://github.com/user/repo2 --output-dir ./output
  python demo.py --help
        """
    )
    parser.add_argument(
        'repositories',
        nargs='+',
        help='One or more Git repository URLs to generate documentation for'
    )
    parser.add_argument(
        '--output-dir',
        type=Path,
        help='Directory to copy generated markdown files to'
    )

    args = parser.parse_args()

    load_dotenv()
    setup_logging()
    logging.info("Starting up…")

    for repo in tqdm(args.repositories, desc="Generating docs for repos"):
        temp_repo_folder = create_temp_repo_folder()
        try:
            generate_docs_remote(repo, temp_repo_folder, local_dev=True)
            
            # Copy markdown files to output directory if specified
            if args.output_dir:
                copy_files(temp_repo_folder, args.output_dir)
                
        except Exception as e:
            logging.error(f"Failed to generate docs for {repo}: {e}")
        # finally:
        #     remove_temp_repo_folder(temp_repo_folder)
