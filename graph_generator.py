import logging
from pathlib import Path

from tqdm import tqdm
from agents.abstraction_agent import AbstractionAgent
from agents.details_agent import DetailsAgent
from agents.tools.utils import clean_dot_file_str
from static_analyzer.pylint_analyze.call_graph_builder import CallGraphBuilder
from static_analyzer.pylint_analyze.structure_graph_builder import StructureGraphBuilder
from static_analyzer.pylint_graph_transform import DotGraphTransformer


class GraphGenerator():
    def __init__(self, repo_location, temp_folder, repo_name, output_dir):
        self.repo_location = repo_location
        self.temp_folder = temp_folder
        self.repo_name = repo_name
        self.output_dir = output_dir

    def generate_analysis(self):
        """
        Generate the graph analysis for the given repository.
        The output is stored in json files in output_dir.
        """
        structures, packages, call_graph_str = self.generate_static_analysis()
        abstraction_agent = AbstractionAgent(repo_dir=self.repo_location, output_dir=self.temp_folder, project_name=self.repo_name)
        abstraction_agent.step_cfg(call_graph_str)
        abstraction_agent.step_source()

        analysis_response = abstraction_agent.generate_analysis()

        details_agent = DetailsAgent(repo_dir=self.repo_location, output_dir=self.temp_folder, project_name=self.repo_name)
        for component in tqdm(analysis_response.components, desc="Analyzing details"):
            # Here I want to filter out based on the qualified names:
            if details_agent.step_subcfg(call_graph_str, component) is None:
                logging.info(f"[Details Agent - ERROR] Failed to analyze subcfg for {component.name}")
                continue
            details_agent.step_cfg(component)
            details_agent.step_enhance_structure(component)
            details_results = details_agent.step_analysis(component)

            if "/" in component.name:
                component.name = component.name.replace("/", "-")
            # now serialize the details_results to a file:
            with open(f"{self.output_dir}/{component.name}.json", "w") as f:
                f.write(details_results.model_dump_json(indent=2))
        with open(f"{self.output_dir}/analysis.json", "w") as f:
            f.write(analysis_response.model_dump_json(indent=2))

    def generate_static_analysis(self):
        dot_suffix = 'structure.dot'
        graph_builder = StructureGraphBuilder(self.repo_location, dot_suffix, self.temp_folder, verbose=True)
        graph_builder.build()
        # Now I have to find and collect the _structure.dot files
        # Scan the current directory for files which end on dot_suffix
        structures = []
        for path in Path('.').rglob(f'*{dot_suffix}'):
            with open(path, 'r') as f:
                structures.append((path.name.split(dot_suffix)[0], f.read()))

        builder = CallGraphBuilder(self.repo_location, max_depth=15, verbose=True)
        builder.build()
        builder.write_dot(f'{self.temp_folder}/call_graph.dot')
        # Now transform the call_graph
        call_graph_str = DotGraphTransformer(f'{self.temp_folder}/call_graph.dot', self.repo_location).transform()
        call_graph_str = clean_dot_file_str(call_graph_str)
        packages = []
        for path in Path('.').rglob(f'{self.temp_folder}/packages_*.dot'):
            with open(path, 'r') as f:
                # The file name is the package name
                package_name = path.name.split('_')[1].split('.dot')[0]
                packages.append((package_name, f.read()))
        return structures, packages, call_graph_str
