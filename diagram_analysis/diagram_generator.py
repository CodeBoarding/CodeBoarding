from __future__ import annotations

import json
import logging
import os
import time
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, as_completed, wait
from contextlib import nullcontext
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from langchain_core.language_models import BaseChatModel

    from diagram_analysis.incremental_models import EscalationLevel, IncrementalSummary, TraceResult
    from diagram_analysis.incremental_types import IncrementalDelta

from agents.abstraction_agent import AbstractionAgent
from agents.agent_responses import AnalysisInsights, Component
from agents.change_status import ChangeStatus
from agents.details_agent import DetailsAgent
from agents.llm_config import initialize_llms
from agents.meta_agent import MetaAgent
from agents.planner_agent import get_expandable_components
from diagram_analysis.analysis_json import (
    FileCoverageReport,
    FileCoverageSummary,
    NotAnalyzedFile,
)
from diagram_analysis.checkpoints import (
    build_file_component_index,
    get_latest_checkpoint,
    load_checkpoint_analysis,
    remove_legacy_manifest,
    save_checkpoint,
)
from diagram_analysis.file_coverage import FileCoverage
from diagram_analysis.io_utils import save_analysis
from diagram_analysis.version import Version
from health.config import initialize_health_dir, load_health_config
from health.runner import run_health_checks
from monitoring import StreamingStatsWriter
from monitoring.mixin import MonitoringMixin
from monitoring.paths import get_monitoring_run_dir
from repo_utils import get_git_commit_hash
from repo_utils.change_detector import ChangeSet, detect_changes
from repo_utils.ignore import RepoIgnoreManager
from repo_utils.method_diff import apply_method_diffs_to_file_index
from repo_utils.parsed_diff import ParsedGitDiff
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
        # Optional pre-started StaticAnalyzer injected by long-lived callers (e.g. the
        # wrapper). When set, pre_analysis() uses it directly instead of creating a new
        # one-shot analyzer via get_static_analysis().
        self._static_analyzer = static_analyzer

        self.details_agent: DetailsAgent | None = None
        self.static_analysis: StaticAnalysisResults | None = None  # Cache static analysis for reuse
        self.abstraction_agent: AbstractionAgent | None = None
        self.meta_agent: MetaAgent | None = None
        self.meta_context: Any | None = None
        self.file_coverage_data: dict | None = None

        self._monitoring_agents: dict[str, MonitoringMixin] = {}
        self.stats_writer: StreamingStatsWriter | None = None
        self._cached_method_changes = ChangeSet()
        self._method_change_resolution_attempted = False
        self._method_diff_base_ref: str | None = "HEAD"
        self.last_incremental_summary: IncrementalSummary | None = None

    @staticmethod
    def _is_rename_only_delta(delta: IncrementalDelta) -> bool:
        return bool(delta.file_deltas) and all(
            file_delta.file_status == ChangeStatus.RENAMED for file_delta in delta.file_deltas
        )

    @classmethod
    def _build_incremental_summary(
        cls,
        *,
        changes: ChangeSet | None = None,
        delta: IncrementalDelta | None = None,
        trace_result: TraceResult | None = None,
        escalation: EscalationLevel | None = None,
        used_llm: bool = False,
    ) -> IncrementalSummary:
        from diagram_analysis.incremental_models import (
            EscalationLevel,
            IncrementalSummary,
            IncrementalSummaryKind,
            TraceStopReason,
        )

        trace_stop_reason = trace_result.stop_reason if trace_result is not None else None

        if delta is None:
            if changes is not None and changes.is_empty():
                return IncrementalSummary(
                    kind=IncrementalSummaryKind.NO_CHANGES,
                    message="No relevant changes were detected, so the existing architecture analysis was reused.",
                )
            return IncrementalSummary(
                kind=IncrementalSummaryKind.COSMETIC_ONLY,
                message="Only cosmetic or non-structural changes were detected, so the architecture analysis was left unchanged.",
            )

        if cls._is_rename_only_delta(delta):
            return IncrementalSummary(
                kind=IncrementalSummaryKind.RENAME_ONLY,
                message="Only file renames were detected, so the architecture analysis was updated without semantic tracing.",
                trace_stop_reason=trace_stop_reason,
                escalation_level=escalation,
            )

        if escalation in (EscalationLevel.FULL, EscalationLevel.ROOT):
            return IncrementalSummary(
                kind=IncrementalSummaryKind.FULL_FALLBACK,
                message="The change scope was too broad for incremental patching, so a full architecture analysis was run.",
                used_llm=used_llm,
                trace_stop_reason=trace_stop_reason,
                escalation_level=escalation,
            )

        if escalation == EscalationLevel.SCOPED:
            return IncrementalSummary(
                kind=IncrementalSummaryKind.SCOPED_REANALYSIS,
                message=trace_result.semantic_impact_summary
                or "The change affected a limited part of the architecture, so only the impacted components were refreshed.",
                used_llm=used_llm,
                trace_stop_reason=trace_stop_reason,
                escalation_level=escalation,
            )

        if trace_result is None:
            if delta.is_purely_additive:
                return IncrementalSummary(
                    kind=IncrementalSummaryKind.ADDITIVE_ONLY,
                    message="New code was added without affecting existing architecture descriptions.",
                    used_llm=used_llm,
                    trace_stop_reason=trace_stop_reason,
                    escalation_level=escalation,
                )
            return IncrementalSummary(
                kind=IncrementalSummaryKind.NO_MATERIAL_IMPACT,
                message="The change did not materially affect component behavior or responsibilities.",
            )

        if trace_result.stop_reason == TraceStopReason.CLOSURE_REACHED and trace_result.impacted_components:
            return IncrementalSummary(
                kind=IncrementalSummaryKind.MATERIAL_IMPACT,
                message=trace_result.semantic_impact_summary
                or "The change materially affected part of the architecture, and the impacted components were updated.",
                used_llm=used_llm,
                trace_stop_reason=trace_result.stop_reason,
                escalation_level=escalation,
            )

        if delta.is_purely_additive:
            return IncrementalSummary(
                kind=IncrementalSummaryKind.ADDITIVE_ONLY,
                message="New code was added without affecting existing architecture descriptions.",
                used_llm=used_llm,
                trace_stop_reason=trace_result.stop_reason,
                escalation_level=escalation,
            )

        if not used_llm:
            return IncrementalSummary(
                kind=IncrementalSummaryKind.COSMETIC_ONLY,
                message="Only cosmetic or local changes were detected, so semantic tracing was skipped.",
                trace_stop_reason=trace_result.stop_reason,
                escalation_level=escalation,
            )

        return IncrementalSummary(
            kind=IncrementalSummaryKind.NO_MATERIAL_IMPACT,
            message="The change did not materially affect component behavior or responsibilities.",
            used_llm=used_llm,
            trace_stop_reason=trace_result.stop_reason,
            escalation_level=escalation,
        )

    def _resolve_method_level_changes(self, parsed_diff: ParsedGitDiff | None = None) -> ChangeSet:
        """Resolve method-level changes against the configured base ref."""
        if parsed_diff is not None:
            self._cached_method_changes = detect_changes(
                self.repo_location,
                parsed_diff.base_ref,
                parsed_diff.target_ref,
                parsed_diff=parsed_diff,
            )
            self._method_change_resolution_attempted = True
            return self._cached_method_changes

        if self._method_change_resolution_attempted:
            return self._cached_method_changes

        self._method_change_resolution_attempted = True
        if self._method_diff_base_ref is None:
            logger.debug("Method diff detection disabled for this analysis run")
            return self._cached_method_changes

        changes = detect_changes(self.repo_location, self._method_diff_base_ref, "")
        if changes.is_empty():
            logger.debug("No changes detected from %s to the working tree", self._method_diff_base_ref)
            return self._cached_method_changes

        self._cached_method_changes = changes
        return changes

    def _set_method_diff_base_ref(self, base_ref: str | None) -> None:
        self._method_diff_base_ref = base_ref
        self._cached_method_changes = ChangeSet(base_ref=base_ref or "", target_ref="")
        self._method_change_resolution_attempted = False

    def _apply_method_diff_statuses(
        self,
        root_analysis: AnalysisInsights,
        sub_analyses: dict[str, AnalysisInsights],
        parsed_diff: ParsedGitDiff | None = None,
    ) -> None:
        """Annotate file/method status fields using working-tree git diffs."""
        changes = self._resolve_method_level_changes(parsed_diff)
        if changes.is_empty():
            return

        apply_method_diffs_to_file_index(root_analysis.files, changes, self.repo_location)
        self._sync_component_statuses_from_files_index(root_analysis)

        for sub_analysis in sub_analyses.values():
            apply_method_diffs_to_file_index(sub_analysis.files, changes, self.repo_location)
            self._sync_component_statuses_from_files_index(sub_analysis)

    @staticmethod
    def _sync_component_statuses_from_files_index(analysis: AnalysisInsights) -> None:
        """Copy file/method statuses from analysis.files into component.file_methods."""
        for component in analysis.components:
            for file_group in component.file_methods:
                file_entry = analysis.files.get(file_group.file_path)
                if file_entry is None:
                    continue

                file_group.file_status = file_entry.file_status
                method_statuses = {method.qualified_name: method.status for method in file_entry.methods}
                for method in file_group.methods:
                    status = method_statuses.get(method.qualified_name)
                    if status is not None:
                        method.status = status

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

            # Final write of unified analysis.json
            self._apply_method_diff_statuses(analysis, sub_analyses)

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
            remove_legacy_manifest(Path(self.output_dir))

            return analysis_path

    def _save_checkpoint(self) -> None:
        """Save the latest successful analysis as a checkpoint."""
        try:
            checkpoint = save_checkpoint(self.repo_location, Path(self.output_dir), run_id=self.run_id)
            logger.info("Saved checkpoint %s at %s", checkpoint.checkpoint_id, checkpoint.checkpoint_commit)
        except Exception as exc:
            logger.warning("Failed to save checkpoint: %s", exc)

    def generate_analysis_smart(self) -> Path:
        """Run smart analysis against the latest checkpoint and save a new checkpoint."""
        latest_checkpoint = get_latest_checkpoint(self.repo_location, Path(self.output_dir))

        if latest_checkpoint is None:
            logger.info("No checkpoint found; running the first smart analysis without method diff annotations")
            self._set_method_diff_base_ref(None)
        else:
            logger.info(
                "Running smart analysis against checkpoint id=%s ref=%s",
                latest_checkpoint.checkpoint_id,
                latest_checkpoint.checkpoint_ref,
            )
            self._set_method_diff_base_ref(latest_checkpoint.checkpoint_ref)

        analysis_path = self.generate_analysis()
        self._save_checkpoint()
        return analysis_path

    # ------------------------------------------------------------------
    # Incremental analysis (trace-based)
    # ------------------------------------------------------------------
    def generate_analysis_incremental(self) -> Path:
        """Run trace-based incremental analysis against the latest checkpoint.

        Requires an existing baseline checkpoint. Re-runs static analysis,
        detects the delta, traces semantic impact, and patches only the
        affected sub-analyses. Saves a new checkpoint on success.

        Raises ``RuntimeError`` if no baseline checkpoint exists.
        Use ``--reset-baseline`` to re-establish one.
        """
        from diagram_analysis.incremental_models import EscalationLevel, TraceResult
        from diagram_analysis.incremental_tracer import build_trace_plan, classify_scope
        from diagram_analysis.incremental_updater import apply_delta

        self.last_incremental_summary = None
        output_dir = Path(self.output_dir)
        _t_total = time.time()

        # 1. Load baseline and re-run static analysis
        _t = time.time()
        latest_checkpoint, root_analysis, sub_analyses, file_component_index, cfgs = self._load_incremental_baseline(
            output_dir
        )
        logger.info("[TIMING] load_incremental_baseline: %.2fs", time.time() - _t)
        base_ref = latest_checkpoint.checkpoint_ref
        static_analysis = self.static_analysis
        assert static_analysis is not None  # guaranteed by _load_incremental_baseline

        # 2. Detect changes and compute delta
        _t = time.time()
        changes = detect_changes(self.repo_location, base_ref, "")
        logger.info("[TIMING] detect_changes: %.2fs", time.time() - _t)
        parsed_diff = changes.parsed_diff

        _t = time.time()
        delta = self._compute_incremental_delta(
            output_dir, changes, root_analysis, file_component_index, static_analysis
        )
        logger.info("[TIMING] compute_incremental_delta: %.2fs", time.time() - _t)
        if delta is None:
            result = self._restore_or_raise(output_dir)
            self.last_incremental_summary = self._build_incremental_summary(changes=changes)
            return result

        _t = time.time()
        apply_delta(root_analysis, sub_analyses, delta)
        logger.info("[TIMING] apply_delta: %.2fs", time.time() - _t)

        if self._is_rename_only_delta(delta):
            logger.info("Delta only contains file renames; skipping semantic tracing")
            _t = time.time()
            result = self._save_incremental_result(output_dir, root_analysis, sub_analyses, base_ref, parsed_diff)
            logger.info("[TIMING] save_incremental_result: %.2fs", time.time() - _t)
            logger.info("[TIMING] generate_analysis_incremental total: %.2fs", time.time() - _t_total)
            self.last_incremental_summary = self._build_incremental_summary(delta=delta)
            return result

        # 3. Skip LLM setup entirely when deterministic analysis shows no traceable work.
        _t = time.time()
        trace_plan = build_trace_plan(
            delta=delta,
            cfgs=cfgs,
            repo_dir=self.repo_location,
            base_ref=base_ref,
            parsed_diff=parsed_diff,
        )
        logger.info("[TIMING] build_trace_plan: %.2fs", time.time() - _t)

        agent_llm: BaseChatModel | None = None
        parsing_llm: BaseChatModel | None = None
        used_llm = False
        if not trace_plan.groups and not trace_plan.fast_path_impacted_methods:
            logger.info("No traceable changed methods; skipping LLM initialization and semantic tracing")
            trace_result = classify_scope(
                trace_result=TraceResult(),
                file_component_index=file_component_index,
                static_analysis=static_analysis,
                repo_dir=self.repo_location,
            )
        else:
            # 4. Initialize LLMs only when tracing or patching may still need them.
            _t = time.time()
            agent_llm, parsing_llm = initialize_llms()
            used_llm = True
            logger.info("[TIMING] initialize_llms: %.2fs", time.time() - _t)

            _t = time.time()
            trace_result = self._run_semantic_trace(
                delta, cfgs, static_analysis, base_ref, file_component_index, agent_llm, parsing_llm, parsed_diff
            )
            logger.info("[TIMING] run_semantic_trace: %.2fs", time.time() - _t)

        # 5. Check escalation
        _t = time.time()
        escalation = self._determine_escalation(trace_result, root_analysis, changes)
        logger.info("[TIMING] determine_escalation: %.2fs", time.time() - _t)
        if escalation in (EscalationLevel.FULL, EscalationLevel.ROOT):
            logger.info("Escalation %s triggered; falling back to full analysis", escalation.value)
            self.last_incremental_summary = self._build_incremental_summary(
                delta=delta,
                trace_result=trace_result,
                escalation=escalation,
                used_llm=used_llm,
            )
            return self.generate_analysis()

        # 6. Apply changes (patch or scoped re-expansion)
        _t = time.time()
        if escalation == EscalationLevel.SCOPED:
            assert agent_llm is not None and parsing_llm is not None
            logger.info("Scoped escalation: re-running DetailsAgent on affected components")
            affected_ids = {ic.component_id for ic in trace_result.impacted_components}
            self._scoped_reexpansion(root_analysis, sub_analyses, affected_ids, agent_llm, parsing_llm)
        elif trace_result.impacted_components:
            assert agent_llm is not None and parsing_llm is not None
            self._patch_impacted_components(root_analysis, sub_analyses, trace_result, agent_llm, parsing_llm)
        logger.info("[TIMING] apply_changes (patch/re-expansion): %.2fs", time.time() - _t)

        # 7. Finalize: method diff statuses, save analysis and checkpoint
        _t = time.time()
        result = self._save_incremental_result(output_dir, root_analysis, sub_analyses, base_ref, parsed_diff)
        logger.info("[TIMING] save_incremental_result: %.2fs", time.time() - _t)
        logger.info("[TIMING] generate_analysis_incremental total: %.2fs", time.time() - _t_total)
        self.last_incremental_summary = self._build_incremental_summary(
            delta=delta,
            trace_result=trace_result,
            escalation=escalation,
            used_llm=used_llm,
        )
        return result

    def _load_incremental_baseline(self, output_dir: Path):
        """Load checkpoint, previous analysis, static analysis, and CFGs.

        If no checkpoint exists but an analysis is on disk, a baseline
        checkpoint is created automatically so that incremental analysis
        can proceed without requiring a full re-analysis.

        Returns (checkpoint, root_analysis, sub_analyses, file_component_index, cfgs).
        """
        _t = time.time()
        latest_checkpoint = get_latest_checkpoint(self.repo_location, output_dir)
        if latest_checkpoint is None:
            if not (output_dir / "analysis.json").is_file():
                raise RuntimeError("Incremental analysis requires an existing analysis. " "Run a full analysis first.")
            logger.info("No checkpoint found; creating baseline from existing analysis")
            save_checkpoint(self.repo_location, output_dir, run_id=self.run_id)
            latest_checkpoint = get_latest_checkpoint(self.repo_location, output_dir)
            if latest_checkpoint is None:
                raise RuntimeError("Failed to create baseline checkpoint from existing analysis.")
        logger.info("[TIMING]   get/create checkpoint: %.2fs", time.time() - _t)

        logger.info(
            "Incremental analysis against checkpoint id=%s ref=%s",
            latest_checkpoint.checkpoint_id,
            latest_checkpoint.checkpoint_ref,
        )

        _t = time.time()
        self._ensure_static_analysis()
        logger.info("[TIMING]   ensure_static_analysis: %.2fs", time.time() - _t)
        static_analysis = self.static_analysis
        assert static_analysis is not None  # guaranteed by _ensure_static_analysis

        _t = time.time()
        cfgs = {lang: static_analysis.get_cfg(lang) for lang in static_analysis.get_languages()}
        logger.info("[TIMING]   build CFGs: %.2fs", time.time() - _t)

        _t = time.time()
        loaded = load_checkpoint_analysis(output_dir, latest_checkpoint.checkpoint_id)
        if loaded is None:
            raise RuntimeError(
                f"Failed to load analysis from checkpoint {latest_checkpoint.checkpoint_id}. "
                "Run a full analysis to re-establish the baseline."
            )
        root_analysis, sub_analyses = loaded
        logger.info("[TIMING]   load_checkpoint_analysis: %.2fs", time.time() - _t)

        _t = time.time()
        file_component_index = build_file_component_index(root_analysis)
        logger.info("[TIMING]   build_file_component_index: %.2fs", time.time() - _t)

        return latest_checkpoint, root_analysis, sub_analyses, file_component_index, cfgs

    def _ensure_static_analysis(self) -> None:
        """Populate ``self.static_analysis`` if not already set."""
        if self.static_analysis is None:
            if self._static_analyzer is not None:
                cache_dir = get_cache_dir(self.repo_location)
                self.static_analysis = self._get_static_from_injected_analyzer(cache_dir)
            else:
                self.static_analysis = get_static_analysis(self.repo_location)

    def _compute_incremental_delta(self, output_dir, changes, root_analysis, file_component_index, static_analysis):
        """Build an IncrementalDelta from file-level changes.

        Returns ``None`` when there are no method-level changes (caller should restore).
        """
        from diagram_analysis.incremental_updater import IncrementalUpdater

        if changes.is_empty():
            logger.info("No changes detected since last checkpoint; restoring previous analysis")
            return None

        def symbol_resolver(file_path: str):
            from agents.agent_responses import MethodEntry

            methods: list[MethodEntry] = []
            for lang in static_analysis.get_languages():
                try:
                    cfg = static_analysis.get_cfg(lang)
                except ValueError:
                    continue
                for qname, node in cfg.nodes.items():
                    if node.file_path.rstrip("/").endswith(file_path.rstrip("/")):
                        methods.append(
                            MethodEntry(
                                qualified_name=qname,
                                start_line=node.line_start,
                                end_line=node.line_end,
                                node_type=node.type.name,
                            )
                        )
            return methods

        updater = IncrementalUpdater(
            analysis=root_analysis,
            file_component_index=file_component_index,
            symbol_resolver=symbol_resolver,
            repo_dir=self.repo_location,
        )
        delta = updater.compute_delta(
            added_files=changes.added_files,
            modified_files=changes.modified_files,
            deleted_files=changes.deleted_files,
            changes=changes,
        )

        if not delta.has_changes:
            logger.info("Delta has no method-level changes; restoring previous analysis")
            return None

        return delta

    def _restore_or_raise(self, output_dir: Path) -> Path:
        """Restore latest checkpoint artifacts, or raise if that fails."""
        from diagram_analysis.checkpoints import restore_latest_artifacts

        restored = restore_latest_artifacts(output_dir)
        if restored:
            return restored
        raise RuntimeError("No changes detected and failed to restore checkpoint artifacts")

    def _run_semantic_trace(
        self,
        delta,
        cfgs,
        static_analysis,
        base_ref,
        file_component_index,
        agent_llm,
        parsing_llm,
        parsed_diff: ParsedGitDiff | None = None,
    ):
        """Run the tracing loop and classify scope."""
        from diagram_analysis.incremental_models import TraceConfig
        from diagram_analysis.incremental_tracer import classify_scope, run_trace

        trace_result = run_trace(
            delta=delta,
            cfgs=cfgs,
            static_analysis=static_analysis,
            repo_dir=self.repo_location,
            base_ref=base_ref,
            agent_llm=agent_llm,
            parsing_llm=parsing_llm,
            config=TraceConfig(),
            parsed_diff=parsed_diff,
        )
        return classify_scope(trace_result, file_component_index, static_analysis, self.repo_location)

    def _save_incremental_result(
        self,
        output_dir,
        root_analysis,
        sub_analyses,
        base_ref,
        parsed_diff: ParsedGitDiff | None = None,
    ) -> Path:
        """Apply diff statuses, save analysis, and save checkpoint."""
        self._set_method_diff_base_ref(base_ref)
        self._apply_method_diff_statuses(root_analysis, sub_analyses, parsed_diff)

        analysis_path = save_analysis(
            analysis=root_analysis,
            output_dir=output_dir,
            sub_analyses=sub_analyses,
            repo_name=self.repo_name,
        ).resolve()

        logger.info("Incremental analysis complete. Written to %s", analysis_path)
        self._save_checkpoint()
        return analysis_path

    def _patch_impacted_components(
        self,
        root_analysis: AnalysisInsights,
        sub_analyses: dict[str, AnalysisInsights],
        trace_result: TraceResult,
        agent_llm: BaseChatModel,
        parsing_llm: BaseChatModel,
    ) -> None:
        """Patch impacted sub-analyses using EASE-encoded JSON patches."""
        from diagram_analysis.analysis_patcher import merge_patched_sub_analyses, patch_sub_analysis

        patched: dict[str, AnalysisInsights] = {}
        seen_parents: set[str] = set()
        patch_tasks: list[tuple[str, AnalysisInsights, ImpactedComponent]] = []

        for impact in trace_result.impacted_components:
            parent_id = self._find_parent_sub_analysis(impact.component_id, sub_analyses)
            if parent_id is None:
                logger.warning("No parent sub-analysis found for component %s", impact.component_id)
                continue
            if parent_id in seen_parents:
                continue
            seen_parents.add(parent_id)

            sub = sub_analyses.get(parent_id)
            if sub is None:
                continue
            patch_tasks.append((parent_id, sub, impact))

        if not patch_tasks:
            return

        max_workers = min(len(patch_tasks), os.cpu_count() or 4, 8)
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {
                executor.submit(patch_sub_analysis, sub, parent_id, impact, parsing_llm, agent_llm): parent_id
                for parent_id, sub, impact in patch_tasks
            }
            for future in as_completed(futures):
                parent_id = futures[future]
                try:
                    result = future.result()
                except Exception as exc:
                    logger.error("Patch failed for %s: %s", parent_id, exc)
                    continue
                if result is not None:
                    patched[parent_id] = result

        if patched:
            merge_patched_sub_analyses(sub_analyses, patched)

    @staticmethod
    def _find_parent_sub_analysis(
        component_id: str,
        sub_analyses: dict[str, AnalysisInsights],
    ) -> str | None:
        """Find the sub-analysis that contains a given component_id."""
        # Direct match: the component_id IS a sub-analysis key
        if component_id in sub_analyses:
            return component_id
        # Check if parent (e.g., "1" for "1.2") is a sub-analysis
        parts = component_id.rsplit(".", 1)
        if len(parts) == 2:
            parent = parts[0]
            if parent in sub_analyses:
                return parent
        # Search all sub-analyses for a component with this ID
        for sub_id, sub in sub_analyses.items():
            for comp in sub.components:
                if comp.component_id == component_id:
                    return sub_id
        return None

    def _scoped_reexpansion(
        self,
        root_analysis: AnalysisInsights,
        sub_analyses: dict[str, AnalysisInsights],
        affected_ids: set[str],
        agent_llm: BaseChatModel,
        parsing_llm: BaseChatModel,
    ) -> None:
        """Re-run DetailsAgent only on affected root components."""
        if self.static_analysis is None:
            raise RuntimeError("static_analysis must be populated before scoped re-expansion")
        meta_context = None
        if self.meta_agent is not None:
            meta_context = self.meta_agent.analyze_project_metadata()

        details = DetailsAgent(
            repo_dir=self.repo_location,
            project_name=self.repo_name,
            static_analysis=self.static_analysis,
            meta_context=meta_context,
            agent_llm=agent_llm,
            parsing_llm=parsing_llm,
            run_id=self.run_id,
        )

        for comp in root_analysis.components:
            if comp.component_id not in affected_ids:
                continue
            logger.info("Re-expanding component %s (%s)", comp.component_id, comp.name)
            try:
                new_sub, _ = details.run(comp)
                sub_analyses[comp.component_id] = new_sub
            except Exception as exc:
                logger.error("Failed to re-expand component %s: %s", comp.component_id, exc)

    @staticmethod
    def _determine_escalation(
        trace_result: TraceResult,
        root_analysis: AnalysisInsights,
        changes: ChangeSet,
    ) -> EscalationLevel:
        """Determine escalation level based on trace results and structural signals."""
        from diagram_analysis.incremental_models import EscalationLevel, TraceStopReason

        # Uncertain trace -> scoped escalation
        if trace_result.stop_reason == TraceStopReason.UNCERTAIN:
            return EscalationLevel.SCOPED

        # Large structural changes -> check scope
        total_root = len(root_analysis.components)
        impacted_root_ids = set()
        for ic in trace_result.impacted_components:
            # Extract root component ID (e.g., "1" from "1.2.3")
            root_id = ic.component_id.split(".")[0]
            impacted_root_ids.add(root_id)

        if total_root > 0 and len(impacted_root_ids) / total_root > 0.5:
            return EscalationLevel.ROOT

        # Check for deleted files that were sole members of a component
        deleted_files = set(changes.deleted_files)
        if deleted_files:
            from diagram_analysis.checkpoints import build_file_component_index

            fci = build_file_component_index(root_analysis)
            for comp in root_analysis.components:
                comp_files = set(fci.get_files_for_component(comp.component_id))
                if comp_files and comp_files.issubset(deleted_files):
                    return EscalationLevel.ROOT

        return EscalationLevel.NONE
