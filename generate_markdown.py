import logging
import os
import shutil
import subprocess
from pathlib import Path

from dotenv import load_dotenv
from git import Repo
from tqdm import tqdm

from agents.agent import AnalysisInsights
from agents.tools.utils import clean_dot_file_str
from graph_generator import GraphGenerator
from logging_config import setup_logging
from static_analyzer.pylint_analyze.call_graph_builder import CallGraphBuilder
from static_analyzer.pylint_analyze.structure_graph_builder import StructureGraphBuilder
from static_analyzer.pylint_graph_transform import DotGraphTransformer
from utils import caching_enabled, create_temp_repo_folder, remove_temp_repo_folder
from utils import generate_mermaid
from utils import remote_repo_exists, RepoDontExistError, sanitize_repo_url, NoGithubTokenFoundError

setup_logging()
logger = logging.getLogger(__name__)


# logger.addHandler(AzureLogHandler(connection_string=os.environ["CONNECTION_STRING"]))

def store_token():
    if os.environ['GITHUB_TOKEN']:
        raise NoGithubTokenFoundError()
    logging.info(f"Setting up credentials with token: {os.environ['GITHUB_TOKEN'][:7]}")  # only first 7 for safety
    cred = (
        "protocol=https\n"
        "host=github.com\n"
        f"username=git\n"
        f"password={os.environ['GITHUB_TOKEN']}\n"
        "\n"
    ).encode()
    subprocess.run(["git", "credential", "approve"], input=cred)


def generate_static_analysis(repo_location: Path, temp_repo_folder: Path):
    dot_suffix = 'structure.dot'
    graph_builder = StructureGraphBuilder(repo_location, dot_suffix, temp_repo_folder, verbose=True)
    graph_builder.build()
    # Now I have to find and collect the _structure.dot files
    # Scan the current directory for files which end on dot_suffix
    structures = []
    for path in Path('.').rglob(f'*{dot_suffix}'):
        with open(path, 'r') as f:
            structures.append((path.name.split(dot_suffix)[0], f.read()))

    builder = CallGraphBuilder(repo_location, max_depth=15, verbose=True)
    builder.build()
    builder.write_dot(f'{temp_repo_folder}/call_graph.dot')
    # Now transform the call_graph
    call_graph_str = DotGraphTransformer(f'{temp_repo_folder}/call_graph.dot', repo_location).transform()
    call_graph_str = clean_dot_file_str(call_graph_str)
    packages = []
    for path in Path('.').rglob(f'{temp_repo_folder}/packages_*.dot'):
        with open(path, 'r') as f:
            # The file name is the package name
            package_name = path.name.split('_')[1].split('.dot')[0]
            packages.append((package_name, f.read()))
    return structures, packages, call_graph_str


def clean_files(directory):
    # delete all the .dot files in the directory
    for f in os.listdir(directory):
        if f.endswith('.dot'):
            os.remove(os.path.join(directory, f))
        if f.endswith('.md') and f != "README.md":
            os.remove(os.path.join(directory, f))
    logging.info("Cleaned up old .dot and .md files in %s", directory)


def onboarding_materials_exist(project_name, source_dir="/home/ivan/StartUp/GeneratedOnBoardings/"):
    repo = Repo(source_dir)
    origin = repo.remote(name='origin')
    origin.pull()

    onboarding_repo_path = os.path.join(source_dir, project_name)
    return os.path.isdir(onboarding_repo_path) and len(os.listdir(onboarding_repo_path))


def upload_onboarding_materials(project_name, output_dir, repo_dir="/home/ivan/StartUp/GeneratedOnBoardings/"):
    repo = Repo(repo_dir)
    origin = repo.remote(name='origin')
    origin.pull()

    onboarding_repo_location = os.path.join(repo_dir, project_name)
    if os.path.exists(onboarding_repo_location):
        shutil.rmtree(onboarding_repo_location)
    os.makedirs(onboarding_repo_location)

    for filename in os.listdir(output_dir):
        if filename.endswith('.md') and filename != "README.md":
            shutil.copy(os.path.join(output_dir, filename), os.path.join(onboarding_repo_location, filename))
    # Now commit the changes
    repo.git.add(A=True)  # Equivalent to `git add .`
    repo.index.commit(f"Uploading onboarding materials for {project_name}")
    origin.push()


def generate_docs(repo_name: str, temp_repo_folder: Path):
    clean_files(Path('./'))
    load_dotenv()
    ROOT = os.getenv("ROOT")
    ROOT_RESULT = os.getenv("ROOT_RESULT")
    repo_path = Path(ROOT) / 'repos' / repo_name

    if caching_enabled() and onboarding_materials_exist(repo_name, ROOT_RESULT):
        logging.info(f"Cache hit for '{repo_name}', skipping documentation generation.")
        return

    generator = GraphGenerator(repo_location=repo_path, temp_folder=temp_repo_folder, repo_name=repo_name,
                               output_dir=temp_repo_folder)
    analysis_files = generator.generate_analysis()

    for file in analysis_files:
        with open(file, 'r') as f:
            analysis = AnalysisInsights.model_validate_json(f.read())
            logging.info(f"Generated analysis file: {file}")
            markdown_response = generate_mermaid(analysis, repo_name, link_files=("analysis.json" in file))
            fname = Path(file).name.split(".json")[0]
            fname = "on_boarding" if fname.endswith("analysis") else fname
            with open(f"{temp_repo_folder}/{fname}.md", "w") as f:
                f.write(markdown_response.strip())
    upload_onboarding_materials(repo_name, temp_repo_folder, ROOT_RESULT)


def generate_docs_remote(repo_url: str, temp_repo_folder: str, local_dev=False) -> Path:
    """
    Clone a git repo to target_dir/<repo-name>.
    Returns the Path to the cloned repository.
    """
    if not local_dev:
        store_token()
    repo_name = clone_repository(repo_url)
    generate_docs(repo_name, temp_repo_folder)
    return repo_name


def clone_repository(repo_url: str, target_dir: Path = Path("./repos")):
    repo_url = sanitize_repo_url(repo_url)
    if not remote_repo_exists(repo_url):
        raise RepoDontExistError()

    base = repo_url.rstrip("/").split("/")[-1]
    name, ext = os.path.splitext(base)
    repo_name = name

    dest = target_dir / repo_name
    if dest.exists():
        logging.info(f"Repository {repo_name} already exists at {dest}, pulling latest.")
        repo = Repo(dest)
        repo.remotes.origin.pull()
    else:
        logging.info(f"Cloning {repo_url} into {dest}")
        Repo.clone_from(repo_url, dest)
    logging.info("Cloning finished!")
    return repo_name


if __name__ == "__main__":
    setup_logging()
    logging.info("Starting up…")
    repos = ["https://github.com/browser-use/browser-use"]
    temp_repo_folder = create_temp_repo_folder()
    for repo in tqdm(repos, desc="Generating docs for repos"):
        generate_docs_remote(repo, temp_repo_folder, local_dev=True)
    remove_temp_repo_folder(temp_repo_folder)
