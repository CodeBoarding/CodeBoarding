import logging
import os
from concurrent.futures import ThreadPoolExecutor, as_completed

from tqdm import tqdm

from agents.abstraction_agent import AbstractionAgent
from agents.agent_responses import ValidationInsights
from agents.details_agent import DetailsAgent
from agents.diff_analyzer import DiffAnalyzingAgent
from agents.meta_agent import MetaAgent
from agents.planner_agent import PlannerAgent
from agents.validator_agent import ValidatorAgent
from diagram_analysis.analysis_json import from_analysis_to_json
from diagram_analysis.version import Version
from output_generators.markdown import sanitize
from repo_utils import get_git_commit_hash
from static_analyzer.analysis_result import StaticAnalysisResults
from static_analyzer.lsp_client.client import LSPClient
from static_analyzer.scanner import ProjectScanner

logger = logging.getLogger(__name__)


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
        self.diff_analyzer_agent = None
        self.meta_agent = None
        self.meta_context = None
        self.depth_level = depth_level

    def process_component(self, component):
        """Process a single component and return its output path and any new components to analyze"""
        try:
            # Now before we try doing anything, we need to check if the component already exists:
            update_analysis = self.diff_analyzer_agent.check_for_component_updates(component)
            if update_analysis.update_degree < 4:  # No need to update
                logging.info(f"Component {component.name} does not require update, skipping analysis.")
                analysis = self.diff_analyzer_agent.get_component_analysis(component)
                safe_name = sanitize(component.name)
                output_path = os.path.join(self.output_dir, f"{safe_name}.json")

                with open(output_path, "w") as f:
                    f.write(analysis.model_dump_json(indent=2))
                return self.repo_location / ".codeboarding" / f"{sanitize(component.name)}.json", analysis.components
            elif 4 < update_analysis.update_degree < 8:
                logger.info(f"Component {component.name} requires partial update, applying feedback.")
                analysis = self.diff_analyzer_agent.get_component_analysis(component)
                update_insight = ValidationInsights(is_valid=False, additional_info=update_analysis.feedback)
                analysis = self.details_agent.apply_feedback(analysis, update_insight)
            else:
                logger.info(f"Processing component: {component.name}")
                self.details_agent.step_subcfg(component)
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
                f.write(from_analysis_to_json(analysis, new_components))

            return output_path, new_components
        except Exception as e:
            logging.error(f"Error processing component {component.name}: {e}")
            return None, []

    def pre_analysis(self):
        static_analysis = self.generate_static_analysis()

        self.meta_agent = MetaAgent(repo_dir=self.repo_location, project_name=self.repo_name,
                                    static_analysis=static_analysis)
        meta_context = self.meta_agent.analyze_project_metadata()
        self.details_agent = DetailsAgent(repo_dir=self.repo_location, project_name=self.repo_name,
                                          static_analysis=static_analysis, meta_context=meta_context)
        self.abstraction_agent = AbstractionAgent(repo_dir=self.repo_location, project_name=self.repo_name,
                                                  static_analysis=static_analysis, meta_context=meta_context)
        self.planner_agent = PlannerAgent(repo_dir=self.repo_location, static_analysis=static_analysis)
        self.validator_agent = ValidatorAgent(repo_dir=self.repo_location, static_analysis=static_analysis)
        self.diff_analyzer_agent = DiffAnalyzingAgent(repo_dir=self.repo_location, static_analysis=static_analysis,
                                                      project_name=self.repo_name)

        version_file = os.path.join(self.output_dir, "codeboarding_version.json")
        with open(version_file, "w") as f:
            f.write(Version(commit_hash=get_git_commit_hash(self.repo_location),
                            code_boarding_version="0.1.0").model_dump_json(indent=2))

    def generate_analysis(self):
        """
        Generate the graph analysis for the given repository.
        The output is stored in json files in output_dir.
        Components are analyzed in parallel by level.
        """
        files = []

        if self.details_agent is None or self.abstraction_agent is None or self.planner_agent is None or self.validator_agent is None:
            self.pre_analysis()

        # Generate the initial analysis
        logger.info("Generating initial analysis")

        update_analysis = self.diff_analyzer_agent.check_for_updates()

        if 4 < update_analysis.update_degree < 8:
            # This is feedback from the diff analyzer, we need to apply it to the abstraction agent
            update_insight = ValidationInsights(is_valid=False, additional_info=update_analysis.feedback)
            analysis = self.abstraction_agent.apply_feedback(self.diff_analyzer_agent.get_analysis(), update_insight)
        elif update_analysis.update_degree >= 8:
            analysis = self.abstraction_agent.run()
            feedback = self.validator_agent.run(analysis)
            if not feedback.is_valid:
                analysis = self.abstraction_agent.apply_feedback(analysis, feedback)
        else:
            analysis = self.diff_analyzer_agent.get_analysis()

        assert analysis is not None, "Analysis should not be None at this point"

        # Get the initial components to analyze (level 0)
        current_level_components = self.planner_agent.plan_analysis(analysis)
        logger.info(f"Found {len(current_level_components)} components to analyze at level 0")

        # Save the root analysis
        analysis_path = os.path.join(self.output_dir, "analysis.json")
        with open(analysis_path, "w") as f:
            f.write(from_analysis_to_json(analysis, current_level_components))
        files.append(analysis_path)

        level = 0
        max_workers = min(os.cpu_count() or 4, 8)  # Limit to 8 workers max

        # Process each level of components in parallel
        while current_level_components:
            level += 1
            if level == self.depth_level:
                break
            logger.info(f"Processing level {level} with {len(current_level_components)} components")
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

            logger.info(f"Completed level {level}. Found {len(next_level_components)} components for next level")
            current_level_components = next_level_components

        logger.info(f"Analysis complete. Generated {len(files)} analysis files")
        print("Generated analysis files: %s", [os.path.abspath(file) for file in files])
        return files

    def generate_static_analysis(self):
        results = StaticAnalysisResults()

        scanner = ProjectScanner(self.repo_location)
        programming_langs = scanner.scan()
        clients = LSPClient.create_clients(programming_langs, self.repo_location)
        for client in clients:
            client.start()

            call_graph = client.build_call_graph()
            class_hierarchy = client.build_class_hierarchies()
            package_graph = client.build_package_relations()
            references = client.build_references()

            results.add_references("python", references)
            results.add_cfg("python", call_graph)
            results.add_class_hierarchy("python", class_hierarchy)
            results.add_package_dependencies("python", package_graph)

        return results
