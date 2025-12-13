import json
import logging
import os
import time
from typing import Any
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import nullcontext
from datetime import datetime
from pathlib import Path

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
from monitoring.paths import generate_run_id, get_monitoring_run_dir
from output_generators.markdown import sanitize
from agents.agent import CodeBoardingAgent
from monitoring import StreamingStatsWriter
from monitoring.mixin import MonitoringMixin
from repo_utils import get_git_commit_hash
from static_analyzer import StaticAnalyzer
from static_analyzer.scanner import ProjectScanner
from utils import monitoring_enabled

logger = logging.getLogger(__name__)


class DiagramGenerator:
    def __init__(
        self,
        repo_location: Path,
        temp_folder: Path,
        repo_name: str,
        output_dir: Path,
        depth_level: int,
        static_only: bool = False,
        project_name: str | None = None,
        run_id: str | None = None,
    ):
        self.repo_location = repo_location
        self.temp_folder = temp_folder
        self.repo_name = repo_name
        self.output_dir = output_dir
        self.depth_level = depth_level
        self.static_only = static_only
        self.project_name = project_name
        self.run_id = run_id
        self.details_agent: DetailsAgent | None = None
        self.abstraction_agent: AbstractionAgent | None = None
        self.planner_agent: PlannerAgent | None = None
        self.validator_agent: ValidatorAgent | None = None
        self.diff_analyzer_agent: DiffAnalyzingAgent | None = None
        self.meta_agent: MetaAgent | None = None
        self.meta_context = None
        self.depth_level = depth_level
        self._monitoring_agents: dict[str, MonitoringMixin] = {}
        self.stats_writer: StreamingStatsWriter | None = None

    def process_component(self, component):
        """Process a single component and return its output path and any new components to analyze"""
        try:
            # Now before we try doing anything, we need to check if the component already exists:
            assert self.diff_analyzer_agent is not None
            assert self.details_agent is not None
            assert self.validator_agent is not None
            assert self.planner_agent is not None

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
                analysis = self.details_agent.run(component)
                feedback = self.validator_agent.run(analysis)
                if not feedback.is_valid:
                    analysis = self.details_agent.apply_feedback(analysis, feedback)
            # Get new components to analyze
            new_components = self.planner_agent.plan_analysis(analysis)

            safe_name = sanitize(component.name)
            output_path = os.path.join(self.output_dir, f"{safe_name}.json")

            # Save the analysis result
            self.details_agent.classify_files(component, analysis)
            with open(output_path, "w") as f:
                f.write(from_analysis_to_json(analysis, new_components))

            return output_path, new_components
        except Exception as e:
            logging.error(f"Error processing component {component.name}: {e}")
            return None, []

    def pre_analysis(self):
        analysis_start_time = time.time()
        static_analysis = StaticAnalyzer(self.repo_location).analyze()

        # --- Capture Static Analysis Stats ---
        static_stats: dict[str, Any] = {"repo_name": self.repo_name, "languages": {}}

        # Use ProjectScanner to get accurate LOC counts via tokei
        scanner = ProjectScanner(self.repo_location)
        loc_by_language = {pl.language: pl.size for pl in scanner.scan()}

        for language in static_analysis.get_languages():
            files = static_analysis.get_source_files(language)

            static_stats["languages"][language] = {
                "file_count": len(files),
                "lines_of_code": loc_by_language.get(language, 0),
            }

        if not self.static_only:
            self.meta_agent = MetaAgent(
                repo_dir=self.repo_location, project_name=self.repo_name, static_analysis=static_analysis
            )
            self._monitoring_agents["MetaAgent"] = self.meta_agent
            meta_context = self.meta_agent.analyze_project_metadata()
            self.details_agent = DetailsAgent(
                repo_dir=self.repo_location,
                project_name=self.repo_name,
                static_analysis=static_analysis,
                meta_context=meta_context,
            )
            self._monitoring_agents["DetailsAgent"] = self.details_agent
            self.abstraction_agent = AbstractionAgent(
                repo_dir=self.repo_location,
                project_name=self.repo_name,
                static_analysis=static_analysis,
                meta_context=meta_context,
            )
            self._monitoring_agents["AbstractionAgent"] = self.abstraction_agent

            self.planner_agent = PlannerAgent(repo_dir=self.repo_location, static_analysis=static_analysis)
            self._monitoring_agents["PlannerAgent"] = self.planner_agent

            self.validator_agent = ValidatorAgent(repo_dir=self.repo_location, static_analysis=static_analysis)
            self._monitoring_agents["ValidatorAgent"] = self.validator_agent

            self.diff_analyzer_agent = DiffAnalyzingAgent(
                repo_dir=self.repo_location, static_analysis=static_analysis, project_name=self.repo_name
            )
            self._monitoring_agents["DiffAnalyzerAgent"] = self.diff_analyzer_agent

        version_file = os.path.join(self.output_dir, "codeboarding_version.json")
        with open(version_file, "w") as f:
            f.write(
                Version(
                    commit_hash=get_git_commit_hash(self.repo_location), code_boarding_version="0.2.0"
                ).model_dump_json(indent=2)
            )

        if monitoring_enabled():
            # Create run directory using unified path utilities
            if self.run_id:
                run_id = self.run_id
            else:
                run_name = self.project_name or self.repo_name
                run_id = generate_run_id(name=run_name)

            monitoring_dir = get_monitoring_run_dir(run_id, create=True)
            logger.info(f"Monitoring enabled. Writing stats to {monitoring_dir}")

            # Save code_stats.json
            code_stats_file = monitoring_dir / "code_stats.json"
            with open(code_stats_file, "w") as f:
                json.dump(static_stats, f, indent=2)
            logger.info(f"Written code_stats.json to {code_stats_file}")

            # Initialize streaming writer (handles timing and run_metadata.json)
            self.stats_writer = StreamingStatsWriter(
                monitoring_dir=monitoring_dir,
                agents_dict=self._monitoring_agents,
                repo_name=self.project_name or self.repo_name,
                output_dir=str(self.output_dir),
                start_time=analysis_start_time,
                static_stats=static_stats,
            )

    def generate_analysis(self):
        """
        Generate the graph analysis for the given repository.
        The output is stored in json files in output_dir.
        Components are analyzed in parallel by level.
        """
        files: list[str] = []

        if (
            self.details_agent is None
            or self.abstraction_agent is None
            or self.planner_agent is None
            or self.validator_agent is None
        ):
            self.pre_analysis()

        # Start monitoring (tracks start time)
        monitor = self.stats_writer if self.stats_writer else nullcontext()
        with monitor:
            if self.static_only:
                logger.info("Static analysis only mode enabled. Analysis complete.")
                return files

            # Generate the initial analysis
            logger.info("Generating initial analysis")

            assert self.diff_analyzer_agent is not None
            assert self.abstraction_agent is not None
            assert self.validator_agent is not None
            assert self.planner_agent is not None

            update_analysis = self.diff_analyzer_agent.check_for_updates()

            if 4 < update_analysis.update_degree < 8:
                # This is feedback from the diff analyzer, we need to apply it to the abstraction agent
                update_insight = ValidationInsights(is_valid=False, additional_info=update_analysis.feedback)
                analysis = self.abstraction_agent.apply_feedback(
                    self.diff_analyzer_agent.get_analysis(), update_insight
                )
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
            # Classify files for the root analysis as last step before saving
            self.abstraction_agent.classify_files(analysis)
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
                    for future in tqdm(
                        as_completed(future_to_component), total=len(future_to_component), desc=f"Level {level}"
                    ):
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
