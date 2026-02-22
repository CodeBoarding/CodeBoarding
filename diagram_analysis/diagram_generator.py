import json
import logging
import os
import time
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from contextlib import nullcontext
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agents.abstraction_agent import AbstractionAgent
from agents.agent_responses import AnalysisInsights, Component
from agents.details_agent import DetailsAgent
from agents.llm_config import initialize_llms
from agents.meta_agent import MetaAgent
from agents.planner_agent import plan_analysis
from diagram_analysis.analysis_json import (
    FileCoverageReport,
    FileCoverageSummary,
    NotAnalyzedFile,
)
from diagram_analysis.file_coverage import FileCoverage
from diagram_analysis.incremental import IncrementalUpdater, UpdateAction
from diagram_analysis.incremental.io_utils import save_analysis
from diagram_analysis.manifest import (
    build_manifest_from_analysis,
    manifest_exists,
    save_manifest,
)
from diagram_analysis.version import Version
from health.config import initialize_health_dir, load_health_config
from health.runner import run_health_checks
from monitoring import StreamingStatsWriter
from monitoring.mixin import MonitoringMixin
from monitoring.paths import generate_run_id, get_monitoring_run_dir
from repo_utils import get_git_commit_hash, get_repo_state_hash
from repo_utils.ignore import RepoIgnoreManager
from static_analyzer import get_static_analysis
from static_analyzer.analysis_result import StaticAnalysisResults
from static_analyzer.scanner import ProjectScanner

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
        self.file_coverage_data: dict | None = None

        self._monitoring_agents: dict[str, MonitoringMixin] = {}
        self.stats_writer: StreamingStatsWriter | None = None

    def process_component(
        self, component: Component
    ) -> tuple[str, AnalysisInsights, list[Component]] | tuple[None, None, list]:
        """Process a single component and return its name, sub-analysis, and new components to analyze."""
        try:
            assert self.details_agent is not None

            analysis, subgraph_cluster_results = self.details_agent.run(component)

            # Track whether parent had clusters for expansion decision
            parent_had_clusters = bool(component.source_cluster_ids)

            # Get new components to analyze (deterministic, no LLM)
            new_components = plan_analysis(analysis, parent_had_clusters=parent_had_clusters)

            return component.component_id, analysis, new_components
        except Exception as e:
            logging.error(f"Error processing component {component.name}: {e}")
            return None, None, []

    def _run_health_report(self, static_analysis: StaticAnalysisResults) -> None:
        """Run health checks and write the report to the output directory."""
        health_config_dir = Path(self.output_dir) / "health"
        initialize_health_dir(health_config_dir)
        health_config = load_health_config(health_config_dir)

        health_report = run_health_checks(
            static_analysis,
            self.repo_name,
            config=health_config,
            repo_path=self.repo_location,
        )
        if health_report is not None:
            health_path = os.path.join(self.output_dir, "health", "health_report.json")
            with open(health_path, "w") as f:
                f.write(health_report.model_dump_json(indent=2, exclude_none=True))
            logger.info(f"Health report written to {health_path} (score: {health_report.overall_score:.3f})")
        else:
            logger.warning("Health checks skipped: no languages found in static analysis results")

    def _build_file_coverage(self, scanner: ProjectScanner, static_analysis: StaticAnalysisResults) -> dict:
        """Build file coverage data comparing all text files against analyzed files."""
        ignore_manager = RepoIgnoreManager(self.repo_location)
        coverage = FileCoverage(self.repo_location, ignore_manager)

        # Convert to Path objects for set operations
        all_files = {Path(f) for f in scanner.all_text_files}
        analyzed_files = {Path(f) for f in static_analysis.get_all_source_files()}

        return coverage.build(all_files, analyzed_files)

    def _write_file_coverage(self) -> None:
        """Write file_coverage.json to output directory."""
        if not self.file_coverage_data:
            return

        report = FileCoverageReport(
            version=1,
            generated_at=datetime.now(timezone.utc).isoformat(),
            analyzed_files=self.file_coverage_data["analyzed_files"],
            not_analyzed_files=[NotAnalyzedFile(**entry) for entry in self.file_coverage_data["not_analyzed_files"]],
            summary=FileCoverageSummary(**self.file_coverage_data["summary"]),
        )

        coverage_path = os.path.join(self.output_dir, "file_coverage.json")
        with open(coverage_path, "w") as f:
            f.write(report.model_dump_json(indent=2, exclude_none=True))
        logger.info(f"File coverage report written to {coverage_path}")

    def pre_analysis(self):
        analysis_start_time = time.time()

        # Initialize LLMs before spawning threads so both share the same instances
        agent_llm, parsing_llm = initialize_llms()

        self.meta_agent = MetaAgent(
            repo_dir=self.repo_location,
            project_name=self.repo_name,
            agent_llm=agent_llm,
            parsing_llm=parsing_llm,
        )
        self._monitoring_agents["MetaAgent"] = self.meta_agent

        # Run static analysis and meta analysis in parallel
        with ThreadPoolExecutor(max_workers=2) as executor:
            skip_cache = self.force_full_analysis
            if skip_cache:
                logger.info("Force full analysis: skipping static analysis cache")
            static_future = executor.submit(get_static_analysis, self.repo_location, skip_cache=skip_cache)
            meta_future = executor.submit(self.meta_agent.get_meta_context, refresh=self.force_full_analysis)

            static_analysis = static_future.result()
            meta_context = meta_future.result()

        self.static_analysis = static_analysis

        # --- Capture Static Analysis Stats ---
        static_stats: dict[str, Any] = {"repo_name": self.repo_name, "languages": {}}
        scanner = ProjectScanner(self.repo_location)
        loc_by_language = {pl.language: pl.size for pl in scanner.scan()}
        for language in static_analysis.get_languages():
            files = static_analysis.get_source_files(language)
            static_stats["languages"][language] = {
                "file_count": len(files),
                "lines_of_code": loc_by_language.get(language, 0),
            }

        # Build file coverage data from scanner's all_text_files and analyzed files
        self.file_coverage_data = self._build_file_coverage(scanner, static_analysis)

        self._run_health_report(static_analysis)

        self.details_agent = DetailsAgent(
            repo_dir=self.repo_location,
            project_name=self.repo_name,
            static_analysis=static_analysis,
            meta_context=meta_context,
            agent_llm=agent_llm,
            parsing_llm=parsing_llm,
        )
        self._monitoring_agents["DetailsAgent"] = self.details_agent
        self.abstraction_agent = AbstractionAgent(
            repo_dir=self.repo_location,
            project_name=self.repo_name,
            static_analysis=static_analysis,
            meta_context=meta_context,
            agent_llm=agent_llm,
            parsing_llm=parsing_llm,
        )
        self._monitoring_agents["AbstractionAgent"] = self.abstraction_agent

        version_file = os.path.join(self.output_dir, "codeboarding_version.json")
        with open(version_file, "w") as f:
            f.write(
                Version(
                    commit_hash=get_git_commit_hash(self.repo_location),
                    code_boarding_version="0.2.0",
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

    def _generate_subcomponents(
        self,
        analysis: AnalysisInsights,
        root_components: list[Component],
    ) -> tuple[list[Component], dict[str, AnalysisInsights]]:
        """Generate subcomponents for the given root-level analysis using a frontier queue."""
        max_workers = min(os.cpu_count() or 4, 8)

        expanded_components: list[Component] = []
        sub_analyses: dict[str, AnalysisInsights] = {}

        # Group stats to avoid cluttering the local variable scope
        stats = {"submitted": 0, "completed": 0, "saves": 0, "errors": 0}

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_task: dict[Future, tuple[Component, int]] = {}

            def submit_component(comp: Component, lvl: int):
                future = executor.submit(self.process_component, comp)
                future_to_task[future] = (comp, lvl)
                stats["submitted"] += 1
                logger.debug("Submitted component='%s' at level=%d", comp.name, lvl)

            # 1. Initial Seeding
            if self.depth_level > 1:
                for component in root_components:
                    submit_component(component, 1)

            logger.info(
                "Subcomponent generation started with %d workers. Initial tasks: %d", max_workers, stats["submitted"]
            )

            # 2. Process Queue
            while future_to_task:
                completed_futures, _ = wait(future_to_task.keys(), return_when=FIRST_COMPLETED)

                for future in completed_futures:
                    component, level = future_to_task.pop(future)
                    stats["completed"] += 1

                    try:
                        comp_name, sub_analysis, new_components = future.result()

                        if comp_name and sub_analysis:
                            sub_analyses[comp_name] = sub_analysis
                            expanded_components.append(component)
                            stats["saves"] += 1

                            logger.debug("Saving intermediate analysis for '%s'", comp_name)
                            save_analysis(
                                analysis=analysis,
                                output_dir=Path(self.output_dir),
                                sub_analyses=sub_analyses,
                                repo_name=self.repo_name,
                            )

                        if new_components and level + 1 < self.depth_level:
                            for child in new_components:
                                submit_component(child, level + 1)

                            logger.info("Expanded '%s' with %d new children.", comp_name, len(new_components))

                    except Exception:
                        stats["errors"] += 1
                        logger.exception("Component '%s' generated an exception", component.name)

                logger.info(
                    "Progress: %d completed, %d in flight, %d errors",
                    stats["completed"],
                    len(future_to_task),
                    stats["errors"],
                )

            logger.info("Subcomponent generation complete: %s", stats)

        return expanded_components, sub_analyses

    def generate_analysis(self):
        """
        Generate the graph analysis for the given repository.
        The output is stored in a single analysis.json file in output_dir.
        Components are analyzed in parallel as soon as their parents complete.
        """
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
            root_components = plan_analysis(analysis)
            logger.info(f"Found {len(root_components)} components to analyze at level 1")

            # Process components using a frontier queue: submit children as soon as parent finishes.
            expanded_components, sub_analyses = self._generate_subcomponents(analysis, root_components)

            # Build file coverage summary for metadata
            file_coverage_summary = None
            if self.file_coverage_data:
                s = self.file_coverage_data["summary"]
                file_coverage_summary = FileCoverageSummary(
                    total_files=s["total_files"],
                    analyzed=s["analyzed"],
                    not_analyzed=s["not_analyzed"],
                    not_analyzed_by_reason=s["not_analyzed_by_reason"],
                )

            # Final write of unified analysis.json
            analysis_path = str(
                save_analysis(
                    analysis=analysis,
                    output_dir=Path(self.output_dir),
                    sub_analyses=sub_analyses,
                    repo_name=self.repo_name,
                    file_coverage_summary=file_coverage_summary,
                )
            )

            logger.info(f"Analysis complete. Written unified analysis to {analysis_path}")

            # Write file_coverage.json
            self._write_file_coverage()

            # Save manifest for incremental updates
            self._save_manifest(analysis, expanded_components)

            return [analysis_path]

    def _save_manifest(self, analysis: AnalysisInsights, expanded_components: list) -> None:
        """Save the analysis manifest for incremental updates."""
        try:
            repo_state_hash = get_repo_state_hash(self.repo_location)
            base_commit = get_git_commit_hash(self.repo_location)

            expanded_names = [c.component_id for c in expanded_components]

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

        # Always regenerate the health report when static analysis is available
        if static_analysis:
            self._run_health_report(static_analysis)

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

            # Update file coverage incrementally
            if static_analysis:
                scanner = ProjectScanner(self.repo_location)
                current_analyzed = {Path(f) for f in static_analysis.get_all_source_files()}
                all_text_files = {Path(f) for f in scanner.all_text_files}

                self.file_coverage_data = updater.update_file_coverage(
                    current_analyzed_files=current_analyzed,
                    all_text_files=all_text_files,
                )
                self._write_file_coverage()

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
