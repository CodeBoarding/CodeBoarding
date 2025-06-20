import logging
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from tqdm import tqdm

from agents.abstraction_agent import AbstractionAgent
from agents.details_agent import DetailsAgent
from agents.planner_agent import PlannerAgent
from agents.validator_agent import ValidatorAgent
from markdown_generation import sanitize
from static_analyzer.pylint_analyze.call_graph_builder import CallGraphBuilder
from static_analyzer.pylint_analyze.structure_graph_builder import StructureGraphBuilder
from static_analyzer.pylint_graph_transform import DotGraphTransformer


class DiagramGenerator:
    def __init__(self, repo_location, temp_folder, repo_name, output_dir, depth_level: int):
        self.repo_location = repo_location
        self.temp_folder = temp_folder
        self.repo_name = repo_name
        self.output_dir = output_dir

        self.details_agent = None
        self.abstraction_agent = None
        self.planner_agent = None
        self.validator_agent = None
        self.depth_level = depth_level

    def process_component(self, component):
        """Process a single component and return its output path and any new components to analyze"""
        try:
            logging.info(f"Processing component: {component.name}")
            self.details_agent.step_subcfg(self.call_graph_str, component)
            self.details_agent.step_cfg(component)
            self.details_agent.step_enhance_structure(component)

            analysis = self.details_agent.step_analysis(component)
            feedback = self.validator_agent.run(analysis)
            if not feedback.is_valid:
                analysis = self.details_agent.apply_feedback(analysis, feedback)

            # Get new components to analyze
            new_components = self.planner_agent.plan_analysis(analysis)

            safe_name = sanitize(component.name)
            output_path = os.path.join(self.output_dir, f"{safe_name}.json")

            # Save the analysis result
            with open(output_path, "w") as f:
                f.write(analysis.model_dump_json(indent=2))

            return output_path, new_components
        except Exception as e:
            logging.error(f"Error processing component {component.name}: {e}")
            return None, []

    def generate_analysis(self):
        """
        Generate the graph analysis for the given repository.
        The output is stored in json files in output_dir.
        Components are analyzed in parallel by level.
        """
        files = []
        structures, packages, self.call_graph_str, cfg = self.generate_static_analysis()

        self.details_agent = DetailsAgent(repo_dir=self.repo_location, output_dir=self.temp_folder,
                                          project_name=self.repo_name, cfg=cfg)
        self.abstraction_agent = AbstractionAgent(repo_dir=self.repo_location, output_dir=self.temp_folder,
                                                  project_name=self.repo_name, cfg=cfg)
        self.planner_agent = PlannerAgent(repo_dir=self.repo_location, output_dir=self.temp_folder, cfg=cfg)
        self.validator_agent = ValidatorAgent(repo_dir=self.repo_location, output_dir=self.temp_folder, cfg=cfg)

        # Generate the initial analysis
        logging.info("Generating initial analysis")
        analysis = self.abstraction_agent.run(self.call_graph_str)
        feedback = self.validator_agent.run(analysis)
        if not feedback.is_valid:
            analysis = self.abstraction_agent.apply_feedback(analysis, feedback)

        # Save the root analysis
        analysis_path = os.path.join(self.output_dir, "analysis.json")
        with open(analysis_path, "w") as f:
            f.write(analysis.model_dump_json(indent=2))
        files.append(analysis_path)

        # Get the initial components to analyze (level 0)
        current_level_components = self.planner_agent.plan_analysis(analysis)
        logging.info(f"Found {len(current_level_components)} components to analyze at level 0")

        level = 0
        max_workers = min(os.cpu_count() or 4, 8)  # Limit to 8 workers max

        # Process each level of components in parallel
        while current_level_components:
            level += 1
            if level == self.depth_level:
                break
            logging.info(f"Processing level {level} with {len(current_level_components)} components")
            next_level_components = []

            # Process current level components in parallel
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                # Submit all tasks
                future_to_component = {
                    executor.submit(self.process_component, component): component
                    for component in current_level_components
                }

                # Use tqdm for a progress bar
                for future in tqdm(as_completed(future_to_component),
                                   total=len(future_to_component),
                                   desc=f"Level {level}"):
                    component = future_to_component[future]
                    try:
                        result_path, new_components = future.result()
                        if result_path:
                            files.append(result_path)
                        if new_components:
                            next_level_components.extend(new_components)
                    except Exception as exc:
                        logging.error(f"Component {component.name} generated an exception: {exc}")

            logging.info(f"Completed level {level}. Found {len(next_level_components)} components for next level")
            current_level_components = next_level_components

        logging.info(f"Analysis complete. Generated {len(files)} analysis files")
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
