import os
import shutil
import logging
from pathlib import Path
from dotenv import load_dotenv
from git import Repo, GitCommandError
from tqdm import tqdm
from utils import caching_documentation_enabled, caching_repo_enabled
from agents.abstraction_agent import AbstractionAgent
from agents.details_agent import DetailsAgent
from agents.markdown_enhancement import MarkdownEnhancer
from agents.tools.utils import clean_dot_file_str
from static_analyzer.pylint_analyze.call_graph_builder import CallGraphBuilder
from static_analyzer.pylint_analyze.structure_graph_builder import StructureGraphBuilder
from static_analyzer.pylint_graph_transform import DotGraphTransformer
from logging_config import setup_logging

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
    logger.info("Cleaned up old .dot and .md files in %s", directory)


def onboarding_materials_exist(project_name, source_dir="/home/ivan/StartUp/GeneratedOnBoardings/"):
    repo = Repo(source_dir)
    origin = repo.remote(name='origin')
    origin.pull()

    onboarding_repo_path = os.path.join(source_dir, project_name)
    return os.path.isdir(onboarding_repo_path) and len(os.listdir(onboarding_repo_path))


def push_onboarding_materials(project_name, repo_dir="/home/ivan/StartUp/GeneratedOnBoardings/"):
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


def main(repo_name):
    ROOT = os.getenv("ROOT")
    ROOT_RESULT = os.getenv("ROOT_RESULT")
    repo_dir = Path(ROOT) / 'repos' / repo_name

    if caching_documentation_enabled() and onboarding_materials_exist(repo_name, ROOT_RESULT):
        logging.info(f"Cache hit for '{repo_name}', skipping documentation generation.")
        print(os.getenv("CACHING"))
        return

    structures, packages, call_graph_str = generate_on_boarding_documentation(repo_dir)
    abstraction_agent = AbstractionAgent(ROOT, repo_dir, repo_name)
    abstraction_agent.step_cfg(call_graph_str)
    abstraction_agent.step_source()

    final_response = abstraction_agent.generate_markdown()
    with open("on_boarding.md", "w") as f:
        f.write(final_response.content.strip())

    details_agent = DetailsAgent(ROOT, repo_dir, repo_name)
    for component in tqdm(final_response.components, desc="Analyzing details"):
        # Here I want to filter out based on the qualified names:
        if details_agent.step_subcfg(call_graph_str, component) is None:
            logging.info(f"[Details Agent - ERROR] Failed to analyze subcfg for {component.name}")
            continue
        details_agent.step_cfg(component)
        details_agent.step_enhance_structure(component)
        details_results = details_agent.step_markdown(component)
        if type(details_results) is str:
            content = details_results
        else:
            content = details_results.content
        if "/" in component.name:
            component.name = component.name.replace("/", "-")
        with open(f"{component.name}.md", "w") as f:
            f.write(content)

    # Upload the onboarding materials to the repo

    # Final touches:
    md_enhancer = MarkdownEnhancer()
    current_files = os.listdir("./")
    for file in tqdm(current_files, desc="Enhancing the markdown files"):
        if file.endswith('.md') and file != "README.md":
            with open(file, 'r') as f:
                content = f.read()
            # content = md_enhancer.fix_diagram(content)
            if file.endswith("on_boarding.md"):
                content = md_enhancer.link_components(content, repo_name, current_files)
                with open(file, 'w') as f:
                    f.write(content)

    push_onboarding_materials(repo_name, ROOT_RESULT)


def clone_repository(repo_url: str) -> Path:
    """
    Clone the given repo_url into $ROOT/repos/<repo_name> if it doesn't exist.
    If it does already exist, pull the latest changes instead.
    Returns the Path to the local repo.
    """
    root = Path(os.getenv("ROOT"))
    repos_dir = root / 'repos'
    repos_dir.mkdir(parents=True, exist_ok=True)

    repo_name = Path(repo_url).stem  # strips off ".git" if present
    repo_path = repos_dir / repo_name

    if caching_repo_enabled() and repo_path.exists():
        logging.info(f"Caching enabled and repository '{repo_name}' already exists; skipping clone/pull.")
        return repo_path

    if repo_path.exists():
        try:
            repo = Repo(repo_path)
            origin = repo.remote(name='origin')
            logging.info(f"Repository '{repo_name}' already exists; pulling latest changes…")
            origin.pull()
        except GitCommandError as e:
            logging.error(f"Failed to pull updates for {repo_name}: {e}")
        except Exception as e:
            logging.error(f"Unexpected error when updating {repo_name}: {e}")
    else:
        try:
            logging.info(f"Cloning '{repo_url}' into '{repo_path}'…")
            repo = Repo.clone_from(repo_url, str(repo_path))
        except GitCommandError as e:
            logging.error(f"Failed to clone {repo_url}: {e}")
            raise

    return repo_path


if __name__ == "__main__":
    load_dotenv(override=True)
    setup_logging()
    logger = logging.getLogger(__name__)
    logger.info("Starting up…")
    repo_urls = [
    "https://github.com/torvalds/linux",
]

    for url in tqdm(repo_urls, desc="Generating docs for repos"):
        local_repo_dir = clone_repository(url)
        clean_files(Path('./'))
        main(local_repo_dir)
