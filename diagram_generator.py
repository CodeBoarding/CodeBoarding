import logging
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from tqdm import tqdm

from agents.abstraction_agent import AbstractionAgent
from agents.details_agent import DetailsAgent
from agents.planner_agent import PlannerAgent
from agents.validator_agent import ValidatorAgent
from static_analyzer.pylint_analyze.call_graph_builder import CallGraphBuilder
from static_analyzer.pylint_analyze.structure_graph_builder import StructureGraphBuilder
from static_analyzer.pylint_graph_transform import DotGraphTransformer


class DiagramGenerator:
    def __init__(self, repo_location, temp_folder, repo_name, output_dir):
        self.repo_location = repo_location
        self.temp_folder = temp_folder
        self.repo_name = repo_name
        self.output_dir = output_dir

        self.details_agent = None
        self.abstraction_agent = None
        self.planner_agent = None
        self.validator_agent = None

    def process_component(self, component):
        self.details_agent.step_subcfg(self.call_graph_str, component)
        self.details_agent.step_cfg(component)
        self.details_agent.step_enhance_structure(component)

        details_results = self.details_agent.step_analysis(component)

        if "/" in component.name:
            component.name = component.name.replace("/", "-")

        output_path = os.path.join(self.output_dir, f"{component.name}.json")

        return output_path

    def generate_analysis(self):
        """
        Generate the graph analysis for the given repository.
        The output is stored in json files in output_dir.
        """
        files = []
        structures, packages, self.call_graph_str, cfg = self.generate_static_analysis()

        self.details_agent = DetailsAgent(repo_dir=self.repo_location, output_dir=self.temp_folder,
                                          project_name=self.repo_name, cfg=cfg)
        self.abstraction_agent = AbstractionAgent(repo_dir=self.repo_location, output_dir=self.temp_folder,
                                                  project_name=self.repo_name, cfg=cfg)

        self.planner_agent = PlannerAgent(repo_dir=self.repo_location, output_dir=self.temp_folder, cfg=cfg)
        self.validator_agent = ValidatorAgent(repo_dir=self.repo_location, output_dir=self.temp_folder, cfg=cfg)

        analysis = self.abstraction_agent.run(self.call_graph_str)
        feedback = self.validator_agent.run(analysis)
        if not feedback.is_valid:
            analysis = self.abstraction_agent.apply_feedback(analysis, feedback)

        components_to_expand = self.planner_agent.plan_analysis(analysis)

        with open(f"{self.output_dir}/analysis.json", "w") as f:
            f.write(analysis.model_dump_json(indent=2))
        files.append(f"{self.output_dir}/analysis.json")

        while True:
            if not components_to_expand:
                break
            component = components_to_expand.pop(0)
            analysis = self.details_agent.run(self.call_graph_str, component)
            feedback = self.validator_agent.run(analysis)
            if not feedback.is_valid:
                analysis = self.details_agent.apply_feedback(analysis, feedback)
            components_to_expand.extend(self.planner_agent.plan_analysis(analysis))

            if "/" in component.name:
                component.name = component.name.replace("/", "-")
            out_file = os.path.join(self.output_dir, f"{component.name}.json")
            with open(out_file, "w") as f:
                f.write(analysis.model_dump_json(indent=2))
            files.append(out_file)

        print("Generated analysis files: %s", [os.path.abspath(file) for file in files])
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
        graph_transformer = DotGraphTransformer(f'{self.temp_folder}/call_graph.dot', self.repo_location)
        cfg, call_graph_str = graph_transformer.transform()
        packages = []
        for path in Path('.').rglob(f'{self.temp_folder}/packages_*.dot'):
            with open(path, 'r') as f:
                # The file name is the package name
                package_name = path.name.split('_')[1].split('.dot')[0]
                packages.append((package_name, f.read()))
        return structures, packages, call_graph_str, cfg
