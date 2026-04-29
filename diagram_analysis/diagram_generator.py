import json
import logging
import os
import time
from collections import defaultdict
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from contextlib import nullcontext
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agents.abstraction_agent import AbstractionAgent
from agents.agent_responses import AnalysisInsights, Component, MethodEntry
from agents.details_agent import DetailsAgent
from agents.llm_config import MONITORING_CALLBACK, initialize_llms
from agents.meta_agent import MetaAgent
from agents.planner_agent import get_expandable_components
from diagram_analysis.analysis_json import (
    FileCoverageReport,
    FileCoverageSummary,
    NotAnalyzedFile,
)
from diagram_analysis.file_coverage import FileCoverage
from diagram_analysis.incremental_tracer import run_trace
from diagram_analysis.incremental_updater import IncrementalUpdater, apply_method_delta
from diagram_analysis.io_utils import save_analysis
from diagram_analysis.scope_planner import (
    apply_patch_scopes,
    build_ownership_index,
    derive_patch_scopes,
    normalize_changes_for_delta,
    pick_component_for_file,
)
from diagram_analysis.version import Version
from health.config import initialize_health_dir, load_health_config
from health.runner import run_health_checks
from monitoring import StreamingStatsWriter
from monitoring.mixin import MonitoringMixin
from monitoring.paths import get_monitoring_run_dir
from repo_utils import get_git_commit_hash
from repo_utils.ignore import RepoIgnoreManager
from static_analyzer import StaticAnalyzer, get_static_analysis
from static_analyzer.analysis_result import StaticAnalysisResults
from static_analyzer.scanner import ProjectScanner
from utils import get_cache_dir

logger = logging.getLogger(__name__)


class DiagramGenerator:
    def __init__(
        self,
        repo_location: Path,
        temp_folder: Path,
        repo_name: str,
        output_dir: Path,
        depth_level: int,
        run_id: str,
        log_path: str,
        project_name: str | None = None,
        monitoring_enabled: bool = False,
        static_analyzer: StaticAnalyzer | None = None,
    ):
        self.repo_location = repo_location
        self.temp_folder = temp_folder
        self.repo_name = repo_name
        self.output_dir = output_dir
        self.depth_level = depth_level
        self.project_name = project_name
        self.run_id = run_id
        self.log_path = log_path
        self.monitoring_enabled = monitoring_enabled
        self.force_full_analysis = False  # Set to True to skip incremental updates
        self._static_analyzer = static_analyzer

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
            new_components = get_expandable_components(analysis, parent_had_clusters=parent_had_clusters)

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
            health_path = Path(self.output_dir) / "health" / "health_report.json"
            with open(health_path, "w", encoding="utf-8") as f:
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

        coverage_path = Path(self.output_dir) / "file_coverage.json"
        with open(coverage_path, "w", encoding="utf-8") as f:
            f.write(report.model_dump_json(indent=2, exclude_none=True))
        logger.info(f"File coverage report written to {coverage_path}")

    def _get_static_from_injected_analyzer(
        self, cache_dir: Path | None, skip_cache: bool = False
    ) -> StaticAnalysisResults:
        result = self._static_analyzer.analyze(  # type: ignore[union-attr]
            cache_dir=cache_dir,
            skip_cache=skip_cache,
        )
        result.diagnostics = self._static_analyzer.collected_diagnostics  # type: ignore[union-attr]
        return result

    def pre_analysis(self):
        analysis_start_time = time.time()

        # Initialize LLMs before spawning threads so both share the same instances
        agent_llm, parsing_llm = initialize_llms()

        self.meta_agent = MetaAgent(
            repo_dir=self.repo_location,
            project_name=self.repo_name,
            agent_llm=agent_llm,
            parsing_llm=parsing_llm,
            run_id=self.run_id,
        )
        self._monitoring_agents["MetaAgent"] = self.meta_agent

        def get_static_with_injected_analyzer() -> StaticAnalysisResults:
            cache_dir = None if self.force_full_analysis else get_cache_dir(self.repo_location)
            return self._get_static_from_injected_analyzer(cache_dir, skip_cache=True)

        def get_static_with_new_analyzer() -> StaticAnalysisResults:
            skip_cache = self.force_full_analysis
            if skip_cache:
                logger.info("Force full analysis: skipping static analysis cache")
            return get_static_analysis(self.repo_location, skip_cache=skip_cache)

        # Decide how to obtain static analysis results, then run it in parallel
        # with the meta-context computation so neither blocks the other.
        if self._static_analyzer is not None:
            logger.info("Using injected StaticAnalyzer (clients already running)")
            static_callable = get_static_with_injected_analyzer
        else:
            static_callable = get_static_with_new_analyzer

        with ThreadPoolExecutor(max_workers=2) as executor:
            meta_agent = self.meta_agent
            assert meta_agent is not None
            static_future = executor.submit(static_callable)
            meta_future = executor.submit(meta_agent.analyze_project_metadata, skip_cache=self.force_full_analysis)
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
            run_id=self.run_id,
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

        version_file = Path(self.output_dir) / "codeboarding_version.json"
        with open(version_file, "w", encoding="utf-8") as f:
            f.write(
                Version(
                    commit_hash=get_git_commit_hash(self.repo_location),
                    code_boarding_version="0.2.0",
                ).model_dump_json(indent=2)
            )

        if self.monitoring_enabled:
            monitoring_dir = get_monitoring_run_dir(self.log_path, create=True)
            logger.debug(f"Monitoring enabled. Writing stats to {monitoring_dir}")

            # Save code_stats.json
            code_stats_file = monitoring_dir / "code_stats.json"
            with open(code_stats_file, "w", encoding="utf-8") as f:
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
        commit_hash = get_git_commit_hash(self.repo_location)

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
                                commit_hash=commit_hash,
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

    def generate_analysis(self) -> Path:
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
            root_components = get_expandable_components(analysis)
            logger.info(f"Found {len(root_components)} components to analyze at level 1")

            # Process components using a frontier queue: submit children as soon as parent finishes.
            expanded_components, sub_analyses = self._generate_subcomponents(analysis, root_components)

            analysis_path = save_analysis(
                analysis=analysis,
                output_dir=Path(self.output_dir),
                sub_analyses=sub_analyses,
                repo_name=self.repo_name,
                file_coverage_summary=self._build_file_coverage_summary(),
                commit_hash=get_git_commit_hash(self.repo_location),
            ).resolve()

            logger.info(f"Analysis complete. Written unified analysis to {analysis_path}")

            # Write file_coverage.json
            self._write_file_coverage()

            return analysis_path

    def _normalize_repo_path(self, path: str) -> str:
        posix = path.replace("\\", "/")
        if Path(posix).is_absolute():
            try:
                return Path(posix).resolve().relative_to(self.repo_location.resolve()).as_posix()
            except ValueError:
                return posix
        while posix.startswith("./"):
            posix = posix[2:]
        return posix

    def _collect_method_entries_from_static_analysis(self) -> dict[str, list]:
        assert self.static_analysis is not None
        methods_by_file: dict[str, list[MethodEntry]] = defaultdict(list)

        for language in self.static_analysis.get_languages():
            try:
                cfg = self.static_analysis.get_cfg(language)
            except ValueError:
                continue

            for node in cfg.nodes.values():
                if node.is_callback_or_anonymous():
                    continue
                if not (node.is_callable() or node.is_class()):
                    continue
                file_path = self._normalize_repo_path(node.file_path)

                methods_by_file[file_path].append(
                    MethodEntry(
                        qualified_name=node.fully_qualified_name,
                        start_line=node.line_start,
                        end_line=node.line_end,
                        node_type=node.type.name,
                    )
                )

        for file_path, methods in methods_by_file.items():
            methods.sort(key=lambda method: (method.start_line, method.end_line, method.qualified_name))
            methods_by_file[file_path] = methods

        return methods_by_file

    def _build_file_coverage_summary(self) -> FileCoverageSummary | None:
        if not self.file_coverage_data:
            return None
        summary = self.file_coverage_data["summary"]
        return FileCoverageSummary(
            total_files=summary["total_files"],
            analyzed=summary["analyzed"],
            not_analyzed=summary["not_analyzed"],
            not_analyzed_by_reason=summary["not_analyzed_by_reason"],
        )

    def generate_analysis_incremental(
        self,
        root_analysis: AnalysisInsights,
        sub_analyses: dict[str, AnalysisInsights],
        base_ref: str,
        changes,
    ) -> Path:
        if self.static_analysis is None:
            self.pre_analysis()
        assert self.static_analysis is not None

        ownership_index = build_ownership_index(root_analysis, sub_analyses)
        added_files, modified_files, deleted_files, rename_map = normalize_changes_for_delta(changes)
        methods_by_file = self._collect_method_entries_from_static_analysis()

        updater = IncrementalUpdater(
            analysis=root_analysis,
            symbol_resolver=lambda file_path: methods_by_file.get(self._normalize_repo_path(file_path), []),
            repo_dir=self.repo_location,
            component_resolver=lambda file_path: pick_component_for_file(file_path, ownership_index, rename_map),
        )
        delta = updater.compute_delta(
            added_files=added_files,
            modified_files=modified_files,
            deleted_files=deleted_files,
            changes=changes,
        )

        apply_method_delta(root_analysis, sub_analyses, delta)
        post_delta_ownership_index = build_ownership_index(root_analysis, sub_analyses)

        agent_llm, parsing_llm = initialize_llms()
        callbacks = [MONITORING_CALLBACK]
        cfgs = {
            language: self.static_analysis.get_cfg(language)
            for language in self.static_analysis.get_languages()
            if self.static_analysis.get_source_files(language)
        }
        trace_result = run_trace(
            delta,
            cfgs,
            self.static_analysis,
            self.repo_location,
            base_ref,
            parsing_llm,
            parsed_diff=getattr(changes, "parsed_diff", None),
            callbacks=callbacks,
        )

        patch_scopes = derive_patch_scopes(
            trace_result,
            root_analysis,
            sub_analyses,
            post_delta_ownership_index,
            rename_map,
        )
        if patch_scopes:
            root_analysis, sub_analyses = apply_patch_scopes(
                root_analysis, sub_analyses, patch_scopes, agent_llm, callbacks
            )

        analysis_path = save_analysis(
            analysis=root_analysis,
            output_dir=Path(self.output_dir),
            sub_analyses=sub_analyses,
            repo_name=self.repo_name,
            file_coverage_summary=self._build_file_coverage_summary(),
            commit_hash=get_git_commit_hash(self.repo_location),
        ).resolve()
        self._write_file_coverage()
        return analysis_path
