import logging
import os
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


if __name__ == "__main__":
    load_dotenv()
    setup_logging()
    logging.info("Starting up…")
    # Load the repos.csv:
    import csv

    companies = set()
    langs = "python"
    with open("/home/ivan/StartUp/CodeBoarding/enhanced_python_repositories_with_languages.csv", "r") as f:
        csv_reader = csv.reader(f)
        rows = list(csv_reader)  # Read all rows into a list

        # Skip the header
    data_rows = rows[1:]
    repos = [(row[2], row[0], row[3]) for row in data_rows]
    # Extract the second column (repo URLs)
    # repos = ["https://github.com/pinterest/pinterest-python-sdk",
    #          "https://github.com/lastmile-ai/mcp-agent"]
    for repo, company, lang in tqdm(repos, desc="Generating docs for repos"):
        temp_repo_folder = create_temp_repo_folder()
        if company in companies:
            continue
        if "python" not in langs.lower():
            continue
        try:
            generate_docs_remote(repo, temp_repo_folder, local_dev=True)
            companies.add(company)
        except Exception as e:
            logging.error(f"Failed to generate docs for {repo}: {e}")
        finally:
            remove_temp_repo_folder(temp_repo_folder)
