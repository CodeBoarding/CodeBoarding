import json
import logging
import os
import time
from typing import Any
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import nullcontext
from pathlib import Path

from tqdm import tqdm

from agents.abstraction_agent import AbstractionAgent
from agents.agent_responses import Component
from agents.details_agent import DetailsAgent
from agents.meta_agent import MetaAgent
from agents.planner_agent import plan_analysis
from diagram_analysis.analysis_json import from_analysis_to_json
from diagram_analysis.manifest import (
    build_manifest_from_analysis,
    save_manifest,
    load_manifest,
    manifest_exists,
)
from diagram_analysis.incremental import IncrementalUpdater, UpdateAction
from diagram_analysis.version import Version
from monitoring.paths import generate_run_id, get_monitoring_run_dir
from output_generators.markdown import sanitize
from monitoring import StreamingStatsWriter
from monitoring.mixin import MonitoringMixin
from repo_utils import get_git_commit_hash, get_repo_state_hash
from static_analyzer import get_static_analysis
from static_analyzer.analysis_result import StaticAnalysisResults
from static_analyzer.scanner import ProjectScanner
from health.runner import run_health_checks
from health.config import initialize_health_dir, load_health_config

logger = logging.getLogger(__name__)


class DiagramGenerator:
    def __init__(
        self,
        repo_location: Path,
        temp_folder: Path,
        repo_name: str,
        output_dir: Path,
        depth_level: int,
        project_name: str | None = None,
        run_id: str | None = None,
        monitoring_enabled: bool = False,
    ):
        self.repo_location = repo_location
        self.temp_folder = temp_folder
        self.repo_name = repo_name
        self.output_dir = output_dir
        self.depth_level = depth_level
        self.project_name = project_name
        self.run_id = run_id
        self.monitoring_enabled = monitoring_enabled
        self.force_full_analysis = False  # Set to True to skip incremental updates

        self.details_agent: DetailsAgent | None = None
        self.static_analysis: StaticAnalysisResults | None = None  # Cache static analysis for reuse
        self.abstraction_agent: AbstractionAgent | None = None
        self.meta_agent: MetaAgent | None = None
        self.meta_context: Any | None = None

        self._monitoring_agents: dict[str, MonitoringMixin] = {}
        self.stats_writer: StreamingStatsWriter | None = None

    def process_component(self, component: Component):
        """Process a single component and return its output path and any new components to analyze"""
        try:
            assert self.details_agent is not None

            analysis, subgraph_cluster_results = self.details_agent.run(component)

            # Track whether parent had clusters for expansion decision
            parent_had_clusters = bool(component.source_cluster_ids)

            # Get new components to analyze (deterministic, no LLM)
            new_components = plan_analysis(analysis, parent_had_clusters=parent_had_clusters)

            safe_name = sanitize(component.name)
            output_path = os.path.join(self.output_dir, f"{safe_name}.json")

            with open(output_path, "w") as f:
                f.write(from_analysis_to_json(analysis, new_components))

            return output_path, new_components
        except Exception as e:
            logging.error(f"Error processing component {component.name}: {e}")
            return None, []

    def pre_analysis(self):
        analysis_start_time = time.time()

        # When force_full is True, skip the cache to perform a full static analysis
        if self.force_full_analysis:
            logger.info("Force full analysis: skipping static analysis cache")
            self.static_analysis = get_static_analysis(self.repo_location, skip_cache=True)
        else:
            self.static_analysis = get_static_analysis(self.repo_location)
        static_analysis = self.static_analysis

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

        # Load health check configuration and initialize health config dir
        health_config_dir = Path(self.output_dir) / "health"
        initialize_health_dir(health_config_dir)
        health_config = load_health_config(health_config_dir)

        health_report = run_health_checks(
            static_analysis, self.repo_name, config=health_config, repo_path=self.repo_location
        )
        if health_report is not None:
            health_path = os.path.join(self.output_dir, "health", "health_report.json")
            with open(health_path, "w") as f:
                f.write(health_report.model_dump_json(indent=2, exclude_none=True))
            logger.info(f"Health report written to {health_path} (score: {health_report.overall_score:.3f})")
        else:
            logger.warning("Health checks skipped: no languages found in static analysis results")

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

        version_file = os.path.join(self.output_dir, "codeboarding_version.json")
        with open(version_file, "w") as f:
            f.write(
                Version(
                    commit_hash=get_git_commit_hash(self.repo_location), code_boarding_version="0.2.0"
                ).model_dump_json(indent=2)
            )

        if self.monitoring_enabled:
            # Create run directory using unified path utilities
            if self.run_id:
                run_id = self.run_id
            else:
                run_name = self.project_name or self.repo_name
                run_id = generate_run_id(name=run_name)

            monitoring_dir = get_monitoring_run_dir(run_id, create=True)
            logger.debug(f"Monitoring enabled. Writing stats to {monitoring_dir}")

            # Save code_stats.json
            code_stats_file = monitoring_dir / "code_stats.json"
            with open(code_stats_file, "w") as f:
                json.dump(static_stats, f, indent=2)
            logger.debug(f"Written code_stats.json to {code_stats_file}")

            # Initialize streaming writer (handles timing and run_metadata.json)
            self.stats_writer = StreamingStatsWriter(
                monitoring_dir=monitoring_dir,
                agents_dict=self._monitoring_agents,
                repo_name=self.project_name or self.repo_name,
                output_dir=str(self.output_dir),
                start_time=analysis_start_time,
            )

    def generate_analysis(self):
        """
        Generate the graph analysis for the given repository.
        The output is stored in json files in output_dir.
        Components are analyzed in parallel by level.
        """
        files: list[str] = []

        if self.details_agent is None or self.abstraction_agent is None:
            self.pre_analysis()

        # Start monitoring (tracks start time)
        monitor = self.stats_writer if self.stats_writer else nullcontext()
        with monitor:
            # Generate the initial analysis
            logger.info("Generating initial analysis")

            assert self.abstraction_agent is not None

            analysis, cluster_results = self.abstraction_agent.run()

            # Get the initial components to analyze (deterministic, no LLM)
            current_level_components = plan_analysis(analysis)
            logger.info(f"Found {len(current_level_components)} components to analyze at level 0")

            # Save the root analysis
            analysis_path = os.path.join(self.output_dir, "analysis.json")
            with open(analysis_path, "w") as f:
                f.write(from_analysis_to_json(analysis, current_level_components))
            files.append(analysis_path)

            level = 0
            max_workers = min(os.cpu_count() or 4, 8)  # Limit to 8 workers max

            # Track components that were actually expanded (have sub-analysis files)
            expanded_components: list = []

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
                                # Track this component as expanded (has sub-analysis file)
                                expanded_components.append(component)
                            if new_components:
                                next_level_components.extend(new_components)
                        except Exception as exc:
                            logging.error(f"Component {component.name} generated an exception: {exc}")

                logger.info(f"Completed level {level}. Found {len(next_level_components)} components for next level")
                current_level_components = next_level_components

            logger.info(f"Analysis complete. Generated {len(files)} analysis files")
            print("Generated analysis files: %s", [os.path.abspath(file) for file in files])

            # Save manifest for incremental updates
            self._save_manifest(analysis, expanded_components)

            return files

    def _save_manifest(self, analysis, expanded_components: list) -> None:
        """Save the analysis manifest for incremental updates."""
        try:
            repo_state_hash = get_repo_state_hash(self.repo_location)
            base_commit = get_git_commit_hash(self.repo_location)

            expanded_names = [c.name for c in expanded_components]

            manifest = build_manifest_from_analysis(
                analysis=analysis,
                repo_state_hash=repo_state_hash,
                base_commit=base_commit,
                expanded_components=expanded_names,
            )

            save_manifest(manifest, self.output_dir)
            logger.info(f"Saved manifest with {len(manifest.file_to_component)} file mappings")
        except Exception as e:
            logger.warning(f"Failed to save manifest: {e}")

    def try_incremental_update(self) -> list[str] | None:
        """
        Attempt an incremental update if possible.

        Returns:
            List of updated file paths if incremental update succeeded,
            None if full analysis is needed.
        """
        if self.force_full_analysis:
            logger.info("Force full analysis requested, skipping incremental check")
            return None

        if not manifest_exists(self.output_dir):
            logger.info("No existing manifest, full analysis required")
            return None

        # For UPDATE_COMPONENTS action, we need static analysis to properly
        # recompute file assignments with cluster matching. Load it first.
        static_analysis = None
        try:
            static_analysis = get_static_analysis(self.repo_location)
            logger.info("Loaded static analysis for incremental update")
        except Exception as e:
            logger.warning(f"Could not load static analysis: {e}")

        updater = IncrementalUpdater(
            repo_dir=self.repo_location,
            output_dir=self.output_dir,
            static_analysis=static_analysis,
            force_full=self.force_full_analysis,
        )

        if not updater.can_run_incremental():
            return None

        impact = updater.analyze()

        # Log the impact for user visibility
        logger.info(f"Incremental update impact: {impact.action.value}")

        if impact.action == UpdateAction.NONE:
            logger.info("No changes detected, analysis is up to date")
            return [str(self.output_dir / "analysis.json")]

        # For structural changes, recompute which components are actually affected
        # after static analysis has been updated with cluster matching
        if impact.action == UpdateAction.UPDATE_COMPONENTS and static_analysis:
            logger.info("Recomputing affected components with updated cluster assignments...")
            updater.recompute_dirty_components(static_analysis)

        if updater.execute():
            logger.info("Incremental update completed successfully")
            return [str(self.output_dir / "analysis.json")]

        # Incremental update failed or not possible
        logger.info("Incremental update not possible, falling back to full analysis")
        return None

    def generate_analysis_smart(self) -> list[str]:
        """
        Smart analysis that tries incremental first, falls back to full.

        This is the recommended entry point for analysis.
        """
        # Try incremental update first
        result = self.try_incremental_update()
        if result is not None:
            return result

        # Fall back to full analysis
        return self.generate_analysis()
