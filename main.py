import logging
import os
import shutil
import subprocess
from pathlib import Path

from dotenv import load_dotenv
from git import Repo
from tqdm import tqdm

from agents.abstraction_agent import AbstractionAgent
from agents.details_agent import DetailsAgent
from agents.tools.utils import clean_dot_file_str
from logging_config import setup_logging
from static_analyzer.pylint_analyze.call_graph_builder import CallGraphBuilder
from static_analyzer.pylint_analyze.structure_graph_builder import StructureGraphBuilder
from static_analyzer.pylint_graph_transform import DotGraphTransformer
from utils import caching_enabled
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


def generate_on_boarding_documentation(repo_location: Path):
    dot_suffix = 'structure.dot'
    graph_builder = StructureGraphBuilder(repo_location, dot_suffix, verbose=True)
    graph_builder.build()
    # Now I have to find and collect the _structure.dot files
    # Scan the current directory for files which end on dot_suffix
    structures = []
    for path in Path('.').rglob(f'*{dot_suffix}'):
        with open(path, 'r') as f:
            structures.append((path.name.split(dot_suffix)[0], f.read()))

    builder = CallGraphBuilder(repo_location, max_depth=15, verbose=True)
    builder.build()
    builder.write_dot('./call_graph.dot')
    # Now transform the call_graph
    call_graph_str = DotGraphTransformer('./call_graph.dot', repo_location).transform()
    call_graph_str = clean_dot_file_str(call_graph_str)
    packages = []
    for path in Path('.').rglob(f'packages_*.dot'):
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


def upload_onboarding_materials(project_name, repo_dir="/home/ivan/StartUp/GeneratedOnBoardings/"):
    repo = Repo(repo_dir)
    origin = repo.remote(name='origin')
    origin.pull()

    onboarding_repo_location = os.path.join(repo_dir, project_name)
    if os.path.exists(onboarding_repo_location):
        shutil.rmtree(onboarding_repo_location)
    os.makedirs(onboarding_repo_location)

    for filename in os.listdir("./"):
        if filename.endswith('.md') and filename != "README.md":
            shutil.copy(filename, os.path.join(onboarding_repo_location, filename))
    # Now commit the changes
    repo.git.add(A=True)  # Equivalent to `git add .`
    repo.index.commit(f"Uploading onboarding materials for {project_name}")
    origin.push()


def generate_docs(repo_name):
    clean_files(Path('./'))
    load_dotenv()
    ROOT = os.getenv("ROOT")
    ROOT_RESULT = os.getenv("ROOT_RESULT")
    repo_path = Path(ROOT) / 'repos' / repo_name
    repo_folder = Path(ROOT) / 'repos'

    if caching_enabled() and onboarding_materials_exist(repo_name, ROOT_RESULT):
        logging.info(f"Cache hit for '{repo_name}', skipping documentation generation.")
        return

    structures, packages, call_graph_str = generate_on_boarding_documentation(repo_path)
    abstraction_agent = AbstractionAgent(ROOT, repo_folder, repo_name)
    abstraction_agent.step_cfg(call_graph_str)
    abstraction_agent.step_source()

    final_response = abstraction_agent.generate_markdown()
    markdown_response = generate_mermaid(final_response, repo_name)

    with open("on_boarding.md", "w") as f:
        f.write(markdown_response.strip())

    details_agent = DetailsAgent(ROOT, repo_path, repo_name)
    for component in tqdm(final_response.components, desc="Analyzing details"):
        # Here I want to filter out based on the qualified names:
        if details_agent.step_subcfg(call_graph_str, component) is None:
            logging.info(f"[Details Agent - ERROR] Failed to analyze subcfg for {component.name}")
            continue
        details_agent.step_cfg(component)
        details_agent.step_enhance_structure(component)
        details_results = details_agent.step_markdown(component)

        details_markdown = generate_mermaid(details_results)
        if "/" in component.name:
            component.name = component.name.replace("/", "-")
        with open(f"{component.name}.md", "w") as f:
            f.write(details_markdown)

    upload_onboarding_materials(repo_name, ROOT_RESULT)


def generate_docs_remote(repo_url: str, local_dev=False) -> Path:
    """
    Clone a git repo to target_dir/<repo-name>.
    Returns the Path to the cloned repository.
    """
    if not local_dev:
        store_token()
    repo_name = clone_repository(repo_url)
    generate_docs(repo_name)
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
    for repo in tqdm(repos, desc="Generating docs for repos"):
        generate_docs_remote(repo, local_dev=True)
