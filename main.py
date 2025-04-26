import os
import shutil
from pathlib import Path

from git import Repo
from tqdm import tqdm

from agents.abstraction_agent import AbstractionAgent
from agents.details_agent import DetailsAgent
from static_analyzer.pylint_analyze.call_graph_builder import CallGraphBuilder
from static_analyzer.pylint_analyze.structure_graph_builder import StructureGraphBuilder
from static_analyzer.pylint_graph_transform import DotGraphTransformer


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
    print("Files clean up done.")


def upload_onboarding_materials(project_name):
    onboarding_repo = "/home/ivan/StartUp/GeneratedOnBoardings/"
    repo = Repo(onboarding_repo)
    origin = repo.remote(name='origin')
    origin.pull()

    onboarding_repo_location = onboarding_repo + project_name
    if os.path.exists(onboarding_repo_location):
        # remove the directory
        shutil.rmtree(onboarding_repo_location)
    os.makedirs(onboarding_repo_location)
    current_files = os.listdir("./")
    for file in current_files:
        if file.endswith('.md') and file != "README.md":
            shutil.copy(file, os.path.join(onboarding_repo_location, file))
    # Now commit the changes
    repo.git.add(A=True)  # Equivalent to `git add .`
    repo.index.commit(f"Uploading onboarding materials for {project_name}")
    origin.push()


def main(repo_name):
    ROOT = "/home/ivan/StartUp/CodeBoarding/repos/"
    root_dir = Path(f"{ROOT}{repo_name}")
    structures, packages, call_graph_str = generate_on_boarding_documentation(root_dir)
    abstraction_agent = AbstractionAgent(root_dir, repo_name)
    abstraction_agent.step_cfg(call_graph_str)
    # for structure in structures:
    #     abstraction_agent.step_structure(f"**{structure[0]}**\n, {structure[1]}")
    analysis_insights = abstraction_agent.step_source()

    markdown_file = abstraction_agent.generate_markdown()
    with open("on_boarding.md", "w") as f:
        f.write(markdown_file.strip())

    # details_agent = DetailsAgent(root_dir, repo_name)
    # for component in tqdm(analysis_insights.abstract_components, desc="Analyzing details"):
    #     # Here I want to filter out based on the qualified names:
    #     cfg_str = DotGraphTransformer("./call_graph.dot", root_dir).subset_transform(component.qualified_names)
    #     details_agent.step_cfg(cfg_str, component)
    #     for structure in structures:
    #         details_agent.step_structure(f"**{structure[0]}**\n, {structure[1]}", component)
    #     details_md = details_agent.step_source()
    #     comp_name = component.name.replace('/', '_')
    #     with open(f"{comp_name}_details.md", "w") as f:
    #         f.write(details_md)

    # Upload the onboarding materials to the repo
    upload_onboarding_materials(repo_name)


if __name__ == "__main__":
    try:
        main("django")
    finally:
        # Clean up the files
        clean_files(Path('./'))
