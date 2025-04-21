from pathlib import Path

from agent import AbstractionAgent
from static_analyzer.pylint_analyze.call_graph_builder import CallGraphBuilder
from static_analyzer.pylint_analyze.structure_graph_builder import StructureGraphBuilder
from static_analyzer.pylint_graph_transform import DotGraphTransformer


def generate_on_boarding_documentation(repo_location: Path, project_name: str):
    dot_suffix = '_structure.dot'
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
    call_graph_str = DotGraphTransformer('./call_graph.dot', project_name).transform()

    return structures, call_graph_str


def main():
    strucutres, call_graph_str = generate_on_boarding_documentation(
        Path("/home/ivan/StartUp/CodeBoarding/repos/WhatWaf"), "lib")
    agent = AbstractionAgent("markitdown")
    agent.step_cfg(call_graph_str)
    for structure in strucutres:
        agent.step_structure(f"**{structure[0]}**\n, {structure[1]}")
    markdown_file = agent.step_source()
    with open("on_boarding.md", "w") as f:
        f.write(markdown_file)

if __name__ == "__main__":
    main()
