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
from agents.agent_responses import AnalysisInsights, Component, MetaAnalysisInsights, MethodEntry
from agents.details_agent import DetailsAgent
from agents.incremental_agent import (
    IncrementalAgent,
    prune_empty_components,
    repopulate_touched_scopes,
    scrub_deleted_files,
    stitch_delta,
)
from agents.llm_config import initialize_llms
from agents.meta_agent import MetaAgent
from agents.planner_agent import get_expandable_components
from diagram_analysis.analysis_json import (
    FileCoverageReport,
    FileCoverageSummary,
    NotAnalyzedFile,
)
from diagram_analysis.cluster_delta import compute_cluster_delta
from diagram_analysis.cluster_snapshot import snapshot_from_analysis
from diagram_analysis.file_coverage import FileCoverage
from diagram_analysis.io_utils import normalize_repo_path, save_analysis
from diagram_analysis.version import Version
from health.config import initialize_health_dir, load_health_config
from health.runner import run_health_checks
from monitoring import StreamingStatsWriter
from monitoring.mixin import MonitoringMixin
from monitoring.paths import get_monitoring_run_dir
from repo_utils import get_git_commit_hash
from repo_utils.change_detector import ChangeSet
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
        # Optional source-tree changeset for the iterative path. When set,
        # the cluster delta drops drift qnames whose file is outside the
        # diff AND outside the prior analysis (see ``compute_cluster_delta``).
        # Set externally before calling ``generate_analysis_incremental``;
        # the workflow function ``run_incremental_workflow`` doesn't need
        # to grow a parameter for it.
        self.changes: ChangeSet | None = None
        self._static_analyzer = static_analyzer

        self.details_agent: DetailsAgent | None = None
        self.static_analysis: StaticAnalysisResults | None = None  # Cache static analysis for reuse
        self.abstraction_agent: AbstractionAgent | None = None
        self.meta_agent: MetaAgent | None = None
        self.meta_context: MetaAnalysisInsights | None = None
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
        self.meta_context = meta_context

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

            commit_hash = get_git_commit_hash(self.repo_location)
            analysis_path = save_analysis(
                analysis=analysis,
                output_dir=Path(self.output_dir),
                sub_analyses=sub_analyses,
                repo_name=self.repo_name,
                file_coverage_summary=self._build_file_coverage_summary(),
                commit_hash=commit_hash,
            ).resolve()

            logger.info(f"Analysis complete. Written unified analysis to {analysis_path}")

            # Write file_coverage.json
            self._write_file_coverage()

            return analysis_path

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
                file_path = normalize_repo_path(node.file_path, self.repo_location)

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
    ) -> Path:
        """Cluster-driven incremental update of an existing ``analysis.json``.

        Mirrors the full-analysis pipeline: a deterministic cluster delta, one
        LLM call to route the delta clusters to existing/new components, then
        the same ``_generate_subcomponents`` frontier queue seeded with only the
        components whose clusters actually changed. Returns the path to the
        updated analysis. The prior CFG clustering is reconstructed inline
        from ``Component.cluster_members`` (no sidecar file); when no
        baseline cluster info is present, falls back to a full
        ``generate_analysis()`` run.
        """
        if self.details_agent is None or self.abstraction_agent is None:
            self.pre_analysis()
        assert self.static_analysis is not None
        assert self.abstraction_agent is not None

        monitor = self.stats_writer if self.stats_writer else nullcontext()
        with monitor:
            # Drop references to deleted files BEFORE any cluster math. Without
            # this step, components keep stale ``file_methods``/``key_entities``
            # that point at files no longer on disk; deleted-file scenarios
            # don't always surface as cluster-id changes (orphan-routed files
            # were never in any cluster), so the cluster pipeline alone can't
            # detect them. After scrub, the prune step at the end naturally
            # removes any component left with no file groups.
            live_files: set[str] = set()
            for language in self.static_analysis.get_languages():
                try:
                    cfg = self.static_analysis.get_cfg(language)
                except (ValueError, KeyError):
                    continue
                for node in cfg.nodes.values():
                    if node.file_path:
                        # CFG node file_paths can be absolute (under a snapshot
                        # worktree) or repo-relative. Components store the
                        # repo-relative form, so normalise both.
                        relative = node.file_path
                        try:
                            relative = str(
                                Path(node.file_path).resolve().relative_to(Path(self.repo_location).resolve())
                            )
                        except (ValueError, OSError):
                            pass
                        live_files.add(relative)
                        live_files.add(node.file_path)
            scrub_deleted_files(root_analysis, sub_analyses, live_files)

            old_snapshot = snapshot_from_analysis(root_analysis, sub_analyses, self.static_analysis)
            if not old_snapshot.all_cluster_ids():
                logger.info("Baseline analysis has no cluster_members; falling back to full analysis.")
                return self.generate_analysis()

            delta = compute_cluster_delta(
                old_snapshot,
                self.static_analysis,
                changes=self.changes,
                repo_dir=self.repo_location,
            )
            if not delta.has_changes:
                logger.info("Cluster delta is empty; rewriting current analysis without re-detailing.")
                prune_empty_components(root_analysis, sub_analyses)
                commit_hash = get_git_commit_hash(self.repo_location)
                analysis_path = save_analysis(
                    analysis=root_analysis,
                    output_dir=Path(self.output_dir),
                    sub_analyses=sub_analyses,
                    repo_name=self.repo_name,
                    file_coverage_summary=self._build_file_coverage_summary(),
                    commit_hash=commit_hash,
                ).resolve()
                self._write_file_coverage()
                return analysis_path

            agent_llm, parsing_llm = initialize_llms()
            incremental_agent = IncrementalAgent(
                repo_dir=self.repo_location,
                static_analysis=self.static_analysis,
                project_name=self.repo_name,
                meta_context=self.meta_context,
                agent_llm=agent_llm,
                parsing_llm=parsing_llm,
            )
            self._monitoring_agents["IncrementalAgent"] = incremental_agent
            delta_cluster_analysis = incremental_agent.step_group_delta(delta, root_analysis, sub_analyses)

            redetail_ids = stitch_delta(root_analysis, sub_analyses, delta_cluster_analysis, delta)

            # Refresh file_methods for redetailed components first (per-component
            # subgraph; siblings stay untouched), then prune anything that ended
            # up with zero owned methods. Order matters: a component's source
            # may be deleted but we only know it's truly empty after rebuilding
            # file_methods from the live CFG.
            repopulate_touched_scopes(
                redetail_ids,
                root_analysis,
                sub_analyses,
                delta.cluster_results(),
                self.abstraction_agent,
            )
            removed_ids = prune_empty_components(root_analysis, sub_analyses)
            if removed_ids:
                redetail_ids -= removed_ids

            # Seed the frontier queue with the redetail set; the same queue used by
            # the full path will recurse into newly expandable children.
            redetail_components = _collect_components_by_id(redetail_ids, root_analysis, sub_analyses)
            if redetail_components:
                _, redetailed_subs = self._generate_subcomponents(root_analysis, redetail_components)
                sub_analyses.update(redetailed_subs)

            # Rebuild the global ``files`` index from the final per-component
            # ``file_methods`` across the entire analysis tree (root + every
            # sub-analysis). Without this, the incremental flow leaves
            # ``analysis.files`` carrying only entries that survived
            # ``scrub_deleted_files`` — newly added files never get bound,
            # which breaks ``analysis.json`` consumers that look up methods
            # via the top-level files map.
            #
            # Why we union subs into root: the full flow's ``AbstractionAgent``
            # runs ``populate_file_methods`` over the FULL CFG so root
            # components carry every project file in their ``file_methods``.
            # The incremental flow never reruns AbstractionAgent — it only
            # touches per-component subgraphs — so root components' own
            # ``file_methods`` lag behind the truth held in deeper
            # sub-analyses. The serialiser at
            # ``analysis_json.build_unified_analysis_json`` reads only
            # ``root_analysis.files`` for the top-level index, so we must
            # surface every depth's files there.
            for sub in sub_analyses.values():
                sub.files = self.abstraction_agent._build_files_index(sub)
            unified_files = self.abstraction_agent._build_files_index(root_analysis)
            for sub in sub_analyses.values():
                for fp, entry in sub.files.items():
                    unified_files.setdefault(fp, entry)
            root_analysis.files = unified_files

            commit_hash = get_git_commit_hash(self.repo_location)
            analysis_path = save_analysis(
                analysis=root_analysis,
                output_dir=Path(self.output_dir),
                sub_analyses=sub_analyses,
                repo_name=self.repo_name,
                file_coverage_summary=self._build_file_coverage_summary(),
                commit_hash=commit_hash,
            ).resolve()
            self._write_file_coverage()
            return analysis_path


def _collect_components_by_id(
    component_ids: set[str],
    root_analysis: AnalysisInsights,
    sub_analyses: dict[str, AnalysisInsights],
) -> list[Component]:
    """Return concrete ``Component`` objects matching the given IDs across root + sub-analyses."""
    if not component_ids:
        return []
    found: list[Component] = []
    seen: set[str] = set()
    for analysis in [root_analysis, *sub_analyses.values()]:
        for component in analysis.components:
            if component.component_id in component_ids and component.component_id not in seen:
                found.append(component)
                seen.add(component.component_id)
    return found
