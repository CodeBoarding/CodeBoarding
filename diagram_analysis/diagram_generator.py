import json
import logging
import os
import time
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, as_completed, wait
from contextlib import nullcontext
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from langchain_core.language_models import BaseChatModel

from agents.abstraction_agent import AbstractionAgent
from agents.agent_responses import AnalysisInsights, Component, MetaAnalysisInsights
from agents.details_agent import DetailsAgent
from agents.llm_config import initialize_llms
from agents.meta_agent import MetaAgent
from agents.planner_agent import get_expandable_components
from diagram_analysis.analysis_json import (
    FileCoverageReport,
    FileCoverageSummary,
    NotAnalyzedFile,
)
from agents.analysis_patcher import merge_patched_sub_analyses, patch_sub_analysis
from diagram_analysis.file_coverage import FileCoverage
from diagram_analysis.incremental.models import (
    DEFAULT_TRACE_CONFIG,
    IncrementalRunResult,
    IncrementalSummary,
    IncrementalSummaryKind,
    TraceConfig,
    TraceResult,
    TraceStopReason,
)
from diagram_analysis.incremental.tracer import classify_scope, run_trace
from diagram_analysis.incremental.delta import IncrementalDelta
from diagram_analysis.incremental.updater import (
    apply_method_delta,
    drop_deltas_for_pruned_components,
    prune_empty_components,
)
from diagram_analysis.io_utils import load_full_analysis, save_analysis
from diagram_analysis.version import Version
from repo_utils.change_detector import ChangeSet
from static_analyzer.graph import CallGraph
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
from utils import ANALYSIS_FILENAME, get_cache_dir

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
        # Optional pre-started StaticAnalyzer injected by long-lived callers (e.g. the
        # wrapper). When set, pre_analysis() uses it directly instead of creating a new
        # one-shot analyzer via get_static_analysis().
        self._static_analyzer = static_analyzer

        self.details_agent: DetailsAgent | None = None
        self.static_analysis: StaticAnalysisResults | None = None  # Cache static analysis for reuse
        self.abstraction_agent: AbstractionAgent | None = None
        self.meta_agent: MetaAgent | None = None
        self.meta_context: MetaAnalysisInsights | None = None
        self.file_coverage_data: dict[str, Any] | None = None
        self.agent_llm: BaseChatModel | None = None
        self.parsing_llm: BaseChatModel | None = None

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
        self.agent_llm = agent_llm
        self.parsing_llm = parsing_llm

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
            return self._get_static_from_injected_analyzer(cache_dir, skip_cache=self.force_full_analysis)

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

            analysis_path = save_analysis(
                analysis=analysis,
                output_dir=Path(self.output_dir),
                sub_analyses=sub_analyses,
                repo_name=self.repo_name,
                file_coverage_summary=file_coverage_summary,
            ).resolve()

            logger.info(f"Analysis complete. Written unified analysis to {analysis_path}")

            # Write file_coverage.json
            self._write_file_coverage()

            return analysis_path

    # ------------------------------------------------------------------
    # Semantic incremental pass
    # ------------------------------------------------------------------
    def _collect_cfgs(self) -> dict[str, CallGraph]:
        assert self.static_analysis is not None
        cfgs: dict[str, CallGraph] = {}
        for lang in self.static_analysis.get_languages():
            try:
                cfgs[lang] = self.static_analysis.get_cfg(lang)
            except ValueError:
                continue
        return cfgs

    def _run_component_patches(
        self,
        sub_analyses: dict[str, AnalysisInsights],
        trace_result: TraceResult,
        parsing_llm: BaseChatModel,
    ) -> tuple[dict[str, AnalysisInsights], list[str]]:
        """Patch impacted sub-analyses in parallel. Returns (patched, failed_ids)."""
        patched: dict[str, AnalysisInsights] = {}
        failed: list[str] = []

        impact_targets = [ic for ic in trace_result.impacted_components if ic.component_id in sub_analyses]
        if not impact_targets:
            return patched, failed

        max_workers = min(len(impact_targets), 4)
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {
                executor.submit(
                    patch_sub_analysis,
                    sub_analyses[ic.component_id],
                    ic.component_id,
                    ic,
                    parsing_llm,
                ): ic.component_id
                for ic in impact_targets
            }
            for future in as_completed(futures):
                comp_id = futures[future]
                try:
                    result = future.result()
                except Exception as exc:
                    logger.error("Patch failed for %s: %s", comp_id, exc)
                    failed.append(comp_id)
                    continue
                if result is None:
                    failed.append(comp_id)
                else:
                    patched[comp_id] = result
        return patched, failed

    def generate_analysis_incremental(
        self,
        delta: IncrementalDelta,
        base_ref: str,
        change_set: ChangeSet,
        config: TraceConfig = DEFAULT_TRACE_CONFIG,
    ) -> IncrementalRunResult:
        """Run a semantic incremental update pass.

        Applies the architectural delta in place, traces semantic impact of
        changed methods through the call graph, patches the affected
        sub-analyses via EASE-encoded JSON Patch, and saves the result.

        The caller owns mode selection (cosmetic-only fast paths live in the
        wrapper); this method handles the semantic-tracing path and its
        deterministic fallbacks.
        """
        assert (
            self.details_agent is not None
            and self.abstraction_agent is not None
            and self.static_analysis is not None
            and self.agent_llm is not None
            and self.parsing_llm is not None
        ), "pre_analysis() must be called before generate_analysis_incremental()"

        existing = load_full_analysis(Path(self.output_dir))
        if existing is None:
            return IncrementalRunResult(
                summary=IncrementalSummary(
                    kind=IncrementalSummaryKind.REQUIRES_FULL_ANALYSIS,
                    message="No existing analysis.json; full analysis required.",
                    requires_full_analysis=True,
                ),
            )
        root_analysis, sub_analyses = existing

        if not delta.has_changes:
            return IncrementalRunResult(
                summary=IncrementalSummary(
                    kind=IncrementalSummaryKind.NO_CHANGES,
                    message="No file changes detected.",
                ),
                analysis_path=(Path(self.output_dir) / ANALYSIS_FILENAME).resolve(),
            )

        if delta.needs_reanalysis:
            return IncrementalRunResult(
                summary=IncrementalSummary(
                    kind=IncrementalSummaryKind.REQUIRES_FULL_ANALYSIS,
                    message="Delta references files without a known component; full analysis required.",
                    requires_full_analysis=True,
                ),
            )

        apply_method_delta(root_analysis, sub_analyses, delta)
        removed_component_ids = prune_empty_components(root_analysis, sub_analyses)
        drop_deltas_for_pruned_components(delta, removed_component_ids)

        if delta.is_purely_additive:
            analysis_path = save_analysis(
                analysis=root_analysis,
                output_dir=Path(self.output_dir),
                sub_analyses=sub_analyses,
                repo_name=self.repo_name,
            ).resolve()
            return IncrementalRunResult(
                summary=IncrementalSummary(
                    kind=IncrementalSummaryKind.ADDITIVE_ONLY,
                    message=(
                        f"Pruned {len(removed_component_ids)} empty component(s); " "no semantic patching needed."
                        if removed_component_ids
                        else "Changes are purely additive; no semantic patching needed."
                    ),
                ),
                analysis_path=analysis_path,
            )

        if not delta.needs_semantic_trace:
            analysis_path = save_analysis(
                analysis=root_analysis,
                output_dir=Path(self.output_dir),
                sub_analyses=sub_analyses,
                repo_name=self.repo_name,
            ).resolve()
            return IncrementalRunResult(
                summary=IncrementalSummary(
                    kind=IncrementalSummaryKind.NO_MATERIAL_IMPACT,
                    message=(
                        f"Pruned {len(removed_component_ids)} empty component(s); structural updates applied."
                        if removed_component_ids
                        else "Deletion-only delta; structural updates applied, prose deferred."
                    ),
                    used_llm=False,
                ),
                analysis_path=analysis_path,
            )

        trace_result = run_trace(
            delta=delta,
            cfgs=self._collect_cfgs(),
            static_analysis=self.static_analysis,
            repo_dir=self.repo_location,
            base_ref=base_ref,
            parsing_llm=self.parsing_llm,
            change_set=change_set,
            config=config,
        )

        if trace_result.stop_reason == TraceStopReason.SYNTAX_ERROR:
            return IncrementalRunResult(
                summary=IncrementalSummary(
                    kind=IncrementalSummaryKind.REQUIRES_FULL_ANALYSIS,
                    message="Syntax errors detected; aborting incremental trace.",
                    used_llm=False,
                    trace_stop_reason=trace_result.stop_reason,
                    requires_full_analysis=True,
                ),
                trace_result=trace_result,
            )

        if not trace_result.all_impacted_methods:
            analysis_path = save_analysis(
                analysis=root_analysis,
                output_dir=Path(self.output_dir),
                sub_analyses=sub_analyses,
                repo_name=self.repo_name,
            ).resolve()
            if trace_result.stop_reason == TraceStopReason.COSMETIC_ONLY:
                kind = IncrementalSummaryKind.COSMETIC_ONLY
                message = "Changes are cosmetic-only; no semantic patching needed."
                used_llm = False
            else:
                kind = IncrementalSummaryKind.NO_MATERIAL_IMPACT
                message = "No methods have semantically impacted descriptions."
                used_llm = True
            return IncrementalRunResult(
                summary=IncrementalSummary(
                    kind=kind,
                    message=message,
                    used_llm=used_llm,
                    trace_stop_reason=trace_result.stop_reason,
                ),
                trace_result=trace_result,
                analysis_path=analysis_path,
            )

        classify_scope(
            trace_result,
            root_analysis.file_to_component(),
            self.static_analysis,
            self.repo_location,
        )

        patched, failed = self._run_component_patches(sub_analyses, trace_result, self.parsing_llm)

        # If every component patch failed, leave analysis.json untouched so the
        # incremental baseline does not advance past docs that never updated.
        if failed and not patched:
            return IncrementalRunResult(
                summary=IncrementalSummary(
                    kind=IncrementalSummaryKind.REQUIRES_FULL_ANALYSIS,
                    message=f"All component patches failed ({len(failed)}); full analysis required.",
                    used_llm=True,
                    trace_stop_reason=trace_result.stop_reason,
                    requires_full_analysis=True,
                ),
                trace_result=trace_result,
                failed_component_ids=sorted(failed),
            )

        merge_patched_sub_analyses(sub_analyses, patched)

        analysis_path = save_analysis(
            analysis=root_analysis,
            output_dir=Path(self.output_dir),
            sub_analyses=sub_analyses,
            repo_name=self.repo_name,
        ).resolve()

        # Partial failure: keep the patched portion on disk for user-visible
        # progress, but flag the run as requiring a full reanalysis so the
        # pipeline does not advance the baseline past unpatched components.
        if failed:
            return IncrementalRunResult(
                summary=IncrementalSummary(
                    kind=IncrementalSummaryKind.SCOPED_REANALYSIS,
                    message=(f"Patched {len(patched)} component(s); {len(failed)} failed — full analysis required."),
                    used_llm=True,
                    trace_stop_reason=trace_result.stop_reason,
                    requires_full_analysis=True,
                ),
                trace_result=trace_result,
                patched_component_ids=sorted(patched.keys()),
                failed_component_ids=sorted(failed),
                analysis_path=analysis_path,
            )

        if trace_result.stop_reason == TraceStopReason.UNCERTAIN:
            kind = IncrementalSummaryKind.SCOPED_REANALYSIS
            message = "Impact trace inconclusive; patched the components we could identify."
        else:
            kind = IncrementalSummaryKind.MATERIAL_IMPACT
            message = trace_result.semantic_impact_summary or "Patched impacted sub-analyses."

        return IncrementalRunResult(
            summary=IncrementalSummary(
                kind=kind,
                message=message,
                used_llm=True,
                trace_stop_reason=trace_result.stop_reason,
            ),
            trace_result=trace_result,
            patched_component_ids=sorted(patched.keys()),
            failed_component_ids=sorted(failed),
            analysis_path=analysis_path,
        )
