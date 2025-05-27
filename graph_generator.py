import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from tqdm import tqdm

from agents.abstraction_agent import AbstractionAgent
from agents.details_agent import DetailsAgent
from agents.tools.utils import clean_dot_file_str
from static_analyzer.pylint_analyze.call_graph_builder import CallGraphBuilder
from static_analyzer.pylint_analyze.structure_graph_builder import StructureGraphBuilder
from static_analyzer.pylint_graph_transform import DotGraphTransformer


class GraphGenerator:
    def __init__(self, repo_location, temp_folder, repo_name, output_dir):
        self.repo_location = repo_location
        self.temp_folder = temp_folder
        self.repo_name = repo_name
        self.output_dir = output_dir

        self.details_agent = None
        self.abstraction_agent = None

    def process_component(self, component):
        self.details_agent.step_subcfg(self.call_graph_str, component)
        self.details_agent.step_cfg(component)
        self.details_agent.step_enhance_structure(component)

        details_results = self.details_agent.step_analysis(component)

        if "/" in component.name:
            component.name = component.name.replace("/", "-")

        output_path = os.path.join(self.output_dir, f"{component.name}.json")
        with open(output_path, "w") as f:
            f.write(details_results.model_dump_json(indent=2))

        return output_path

    def generate_analysis(self):
        """
        Generate the graph analysis for the given repository.
        The output is stored in json files in output_dir.
        """
        files = []
        structures, packages, self.call_graph_str = self.generate_static_analysis()

        self.details_agent = DetailsAgent(repo_dir=self.repo_location, output_dir=self.temp_folder,
                                          project_name=self.repo_name)
        self.abstraction_agent = AbstractionAgent(repo_dir=self.repo_location, output_dir=self.temp_folder,
                                                  project_name=self.repo_name)

        self.abstraction_agent.step_cfg(self.call_graph_str)
        self.abstraction_agent.step_source()

        analysis_response = self.abstraction_agent.generate_analysis()

        with ThreadPoolExecutor() as executor:
            futures = [executor.submit(self.process_component, component) for component in
                       analysis_response.components]
            for future in tqdm(as_completed(futures), total=len(futures), desc="Analyzing details"):
                result = future.result()
                if result:
                    files.append(result)

        files.append(f"{self.output_dir}/analysis.json")
        print("Generated analysis files: %s", [os.path.abspath(file) for file in files])
        with open(f"{self.output_dir}/analysis.json", "w") as f:
            f.write(analysis_response.model_dump_json(indent=2))
        return files

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
