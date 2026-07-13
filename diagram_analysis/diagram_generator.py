import json
import logging
import os
import time
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from contextlib import nullcontext
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from langchain_core.language_models import BaseChatModel

from agents.abstraction_agent import AbstractionAgent
from agents.agent_responses import (
    AnalysisInsights,
    Component,
    MetaAnalysisInsights,
)
from agents.cluster_methods_mixin import scoped_snapshot_from_lineage
from agents.details_agent import DetailsAgent
from agents.incremental_agent import (
    IncrementalAgent,
    prune_empty_components,
    remove_deleted_files,
)
from agents.incremental_planning_agent import IncrementalPlanningAgent
from agents.incremental_results import RecursiveScopeUpdateResult
from agents.file_index_models import FileEntry
from agents.llm_config import initialize_llms
from agents.llm_errors import LLMAuthError
from agents.meta_agent import MetaAgent
from agents.planner_agent import get_expandable_components
from agents.relation_edges import index_relation_endpoints
from agents.scope_ids import ROOT_SCOPE_ID
from agents.content_hash import SourceCache, hash_repo_source_files, tree_hash_from_file_hashes
from diagram_analysis.analysis_json import (
    FileCoverageReport,
    FileCoverageSummary,
    NotAnalyzedFile,
)
from diagram_analysis.cluster_delta import (
    ClusterDelta,
    LanguageDelta,
    StructuralClusterDiff,
    compute_cluster_delta,
    structural_diff_from_delta,
)
from diagram_analysis.cluster_snapshot import (
    ClusterSnapshot,
    snapshot_from_static_analysis,
)
from diagram_analysis.exceptions import IncrementalCacheMissingError
from diagram_analysis.file_coverage import FileCoverage
from diagram_analysis.file_index import build_files_index, refresh_method_spans_from_cfg
from diagram_analysis.io_utils import load_analysis_metadata, normalize_repo_path, save_analysis, write_fingerprint
from health.config import initialize_health_dir, load_health_config
from health.runner import run_health_checks
from monitoring import StreamingStatsWriter
from monitoring.mixin import MonitoringMixin
from monitoring.paths import get_monitoring_run_dir
from repo_utils.change_detector import ChangeSet
from repo_utils.ignore import RepoIgnoreManager
from static_analyzer import StaticAnalyzer, get_static_analysis
from static_analyzer.analysis_cache import StaticAnalysisCache
from static_analyzer.analysis_result import StaticAnalysisResults
from static_analyzer.cluster_relations import build_global_relations, is_self_or_descendant
from static_analyzer.constants import Language
from static_analyzer.graph import ClusterResult
from static_analyzer.scanner import ProjectScanner
from telemetry.events import track_analysis

logger = logging.getLogger(__name__)


def _component_depth(component_id: str | None) -> int:
    """Return the absolute diagram depth for a hierarchical component id."""
    if not component_id:
        return 1
    return component_id.count(".") + 1


def _component_expansion_seeds(components: list[Component], max_depth: int) -> list[tuple[Component, int]]:
    """Return components that may still be expanded, paired with absolute depth."""
    return [
        (component, depth)
        for component in components
        if (depth := _component_depth(component.component_id)) < max_depth
    ]


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
        changes: ChangeSet | None = None,
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
        # Source-tree changeset for the iterative path. When set, the cluster
        # delta drops drift qnames whose file is outside the diff AND outside
        # the prior analysis (see ``compute_cluster_delta``). ``None`` runs
        # unscoped (no drift filtering).
        self.changes: ChangeSet | None = changes
        # Whole-tree content hash, stamped into the pkl's sibling .sha file as the
        # diff base for the next warm-start (NOT a cache gate). ``pre_analysis``
        # fills it from the live tree when unset; ``None`` is a tag-less save.
        self.source_sha: str | None = None
        # Whole-tree ``{posix_path: sha16}`` fingerprint, computed once per run and
        # reused for source_sha, the sidecar, and every save's source_tree_hash
        # instead of re-walking the tree each time.
        self._source_tree_fingerprint: dict[str, str] | None = None
        self._static_analyzer = static_analyzer

        self.details_agent: DetailsAgent | None = None
        self.static_analysis: StaticAnalysisResults | None = None  # Cache static analysis for reuse
        self.abstraction_agent: AbstractionAgent | None = None
        self.meta_agent: MetaAgent | None = None
        self.incremental_planning_agent: IncrementalPlanningAgent | None = None
        self.incremental_agent: IncrementalAgent | None = None
        self.meta_context: MetaAnalysisInsights | None = None
        self.file_coverage_data: dict | None = None

        self._monitoring_agents: dict[str, MonitoringMixin] = {}
        self.stats_writer: StreamingStatsWriter | None = None

    @track_analysis
    def process_component(
        self, component: Component
    ) -> tuple[str, AnalysisInsights, list[Component]] | tuple[None, None, list]:
        return self._process_component(component)

    def _process_component(
        self, component: Component
    ) -> tuple[str, AnalysisInsights, list[Component]] | tuple[None, None, list]:
        """Process a single component and return its name, sub-analysis, and new components to analyze."""
        try:
            assert self.details_agent is not None

            analysis, _ = self.details_agent.run(component)

            # Track whether parent had clusters for expansion decision
            parent_had_clusters = bool(component.source_cluster_ids)

            # Get new components to analyze (deterministic, no LLM)
            new_components = get_expandable_components(analysis, parent_had_clusters=parent_had_clusters)

            return component.component_id, analysis, new_components
        except LLMAuthError:
            # A rejected key fails every component identically; don't swallow it
            # per-component and grind through the rest — abort the whole run.
            raise
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

    def _strip_ignored(
        self,
        analysis: AnalysisInsights,
        sub_analyses: dict[str, AnalysisInsights] | None = None,
    ) -> None:
        """Sweep ``.codeboardingignore``-matched files out of the rendered tree.

        Single chokepoint applied right before every ``save_analysis(...)`` so
        the serialized architecture honors the user's ignore rules, regardless
        of which discovery path (LSP imports, agent clustering, plugin) added
        a file. Other layers (file_monitor, file_coverage, function_size)
        already use ``RepoIgnoreManager``; this extends the same authority to
        the analyzer's persisted output.

        Idempotent. Mutates in place. Empty components are kept (relations may
        reference them); downstream renderers handle zero-method components.
        """
        ignore_manager = RepoIgnoreManager(self.repo_location)
        ignore_manager.strip_ignored(analysis)
        for sub in (sub_analyses or {}).values():
            ignore_manager.strip_ignored(sub)

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

    def _changed_files_for_static_analysis(self) -> set[Path] | None:
        """Absolute changed-file paths from the incremental ChangeSet, or None.

        Incremental analysis always carries a git-free ``ChangeSet`` (the
        fingerprint diff). We hand those files to the static-analysis warm-start
        so it re-LSPs exactly them without shelling out to git. None means "no
        ChangeSet" (a full run) and leaves the warm-start to its own git scoping;
        an empty set means "incremental, nothing changed" and correctly re-LSPs
        zero files instead of falling back to a full re-LSP via git.
        """
        if self.changes is None:
            return None
        rel_paths = self.changes.added_files + self.changes.modified_files + self.changes.deleted_files
        return {(self.repo_location / rel).resolve() for rel in rel_paths}

    def _get_static_with_injected_analyzer(self) -> StaticAnalysisResults:
        """Run the injected analyzer with the configured cache policy."""
        assert self._static_analyzer is not None
        disable_reuse = os.getenv("CODEBOARDING_DISABLE_CACHE_REUSE", "").lower() in ("1", "true", "yes")
        skip_cache = self.force_full_analysis or disable_reuse
        if self.force_full_analysis:
            logger.info("Force full analysis: skipping static analysis cache")
        if disable_reuse:
            logger.info("CODEBOARDING_DISABLE_CACHE_REUSE set; skipping static analysis cache")
        self._static_analyzer.changed_files = self._changed_files_for_static_analysis()
        result = self._static_analyzer.analyze(
            skip_cache=skip_cache,
            source_sha=self.source_sha,
            cache_dir=self.output_dir,
        )
        result.diagnostics = self._static_analyzer.collected_diagnostics
        return result

    def _get_static_with_new_analyzer(self) -> StaticAnalysisResults:
        """Run static analysis with a newly created analyzer."""
        disable_reuse = os.getenv("CODEBOARDING_DISABLE_CACHE_REUSE", "").lower() in ("1", "true", "yes")
        skip_cache = self.force_full_analysis or disable_reuse
        if self.force_full_analysis:
            logger.info("Force full analysis: skipping static analysis cache")
        if disable_reuse:
            logger.info("CODEBOARDING_DISABLE_CACHE_REUSE set; skipping static analysis cache")
        return get_static_analysis(
            self.repo_location,
            skip_cache=skip_cache,
            source_sha=self.source_sha,
            cache_dir=self.output_dir,
            changed_files=self._changed_files_for_static_analysis(),
        )

    def _seed_incremental_cluster_cache(self, cluster_results: dict[str, ClusterResult]) -> None:
        """Write post-delta ``cluster_results`` into each language CFG's ``_cluster_cache``.

        On the incremental path the abstraction agent doesn't run, so the live
        partition has to be plumbed in explicitly before ``stop_clients`` saves
        the pkl. ``cluster_snapshot`` reads exclusively from this cache.
        """
        if self.static_analysis is None:
            return
        for language, cr in cluster_results.items():
            try:
                cfg = self.static_analysis.get_cfg(Language(language))
            except (ValueError, KeyError):
                continue
            cfg._cluster_cache = cr
            cfg.record_cluster_paths(cr)

    def _persist_static_analysis_artifact(self) -> None:
        """Persist the post-clustering static-analysis artifact."""
        if self._static_analyzer is not None:
            self._static_analyzer.flush_cache()
            return
        if self.static_analysis is None:
            return
        StaticAnalysisCache(self.output_dir, self.repo_location).save(self.static_analysis, source_sha=self.source_sha)

    def _source_tree_fingerprint_map(self) -> dict[str, str]:
        """The whole-tree fingerprint, fingerprinting on first use if pre_analysis didn't."""
        if self._source_tree_fingerprint is None:
            self._source_tree_fingerprint = hash_repo_source_files(self.repo_location)
        return self._source_tree_fingerprint

    def _source_tree_hash(self) -> str:
        """The source-tree version key aggregated from the cached fingerprint."""
        return tree_hash_from_file_hashes(self._source_tree_fingerprint_map())

    def _initialize_meta_agent(self, agent_llm: BaseChatModel, parsing_llm: BaseChatModel) -> None:
        """Initialize the metadata agent needed before the other agents."""
        self.meta_agent = MetaAgent(
            repo_dir=self.repo_location,
            project_name=self.repo_name,
            agent_llm=agent_llm,
            parsing_llm=parsing_llm,
            run_id=self.run_id,
        )
        self._monitoring_agents["MetaAgent"] = self.meta_agent

    def _initialize_agents(
        self,
        static_analysis: StaticAnalysisResults,
        meta_context: MetaAnalysisInsights,
        agent_llm: BaseChatModel,
        parsing_llm: BaseChatModel,
    ) -> None:
        """Initialize agents that depend on static analysis and project metadata."""
        self.details_agent = DetailsAgent(
            repo_dir=self.repo_location,
            project_name=self.repo_name,
            static_analysis=static_analysis,
            meta_context=meta_context,
            agent_llm=agent_llm,
            parsing_llm=parsing_llm,
            run_id=self.run_id,
        )
        self.abstraction_agent = AbstractionAgent(
            repo_dir=self.repo_location,
            project_name=self.repo_name,
            static_analysis=static_analysis,
            meta_context=meta_context,
            agent_llm=agent_llm,
            parsing_llm=parsing_llm,
        )
        self.incremental_planning_agent = IncrementalPlanningAgent(
            repo_dir=self.repo_location,
            static_analysis=static_analysis,
            project_name=self.repo_name,
            meta_context=meta_context,
            agent_llm=agent_llm,
            parsing_llm=parsing_llm,
            changes=self.changes,
        )
        self.incremental_agent = IncrementalAgent(
            repo_dir=self.repo_location,
            static_analysis=static_analysis,
            project_name=self.repo_name,
            meta_context=meta_context,
            agent_llm=agent_llm,
            parsing_llm=parsing_llm,
            changes=self.changes,
        )
        self._monitoring_agents.update(
            {
                "DetailsAgent": self.details_agent,
                "AbstractionAgent": self.abstraction_agent,
                "IncrementalPlanningAgent": self.incremental_planning_agent,
                "IncrementalAgent": self.incremental_agent,
            }
        )

    def pre_analysis(self):
        analysis_start_time = time.time()

        # Fingerprint the whole tree once; source_sha, the sidecar, and every
        # save's source_tree_hash reuse it instead of re-walking per call.
        self._source_tree_fingerprint = hash_repo_source_files(self.repo_location)
        # Compute the source-state tag from live source when a caller didn't
        # supply one, so the pkl always gets a .sha sibling for the next
        # warm-start — no caller has to thread source_sha in.
        if self.source_sha is None:
            self.source_sha = self._source_tree_hash() or None

        # Initialize LLMs before spawning threads so both share the same instances
        agent_llm, parsing_llm = initialize_llms()

        self._initialize_meta_agent(agent_llm, parsing_llm)

        # Decide how to obtain static analysis results, then run it in parallel
        # with the meta-context computation so neither blocks the other.
        if self._static_analyzer is not None:
            logger.info("Using injected StaticAnalyzer (clients already running)")
            static_callable = self._get_static_with_injected_analyzer
        else:
            static_callable = self._get_static_with_new_analyzer

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

        self._initialize_agents(static_analysis, meta_context, agent_llm, parsing_llm)

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
        """Generate subcomponents using absolute component depth and a frontier queue."""
        max_workers = min(os.cpu_count() or 4, 8)

        expanded_components: list[Component] = []
        sub_analyses: dict[str, AnalysisInsights] = {}

        # Group stats to avoid cluttering the local variable scope
        stats = {"submitted": 0, "completed": 0, "saves": 0, "errors": 0}

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_task: dict[Future, tuple[Component, int]] = {}

            def submit_component(comp: Component, lvl: int):
                future = executor.submit(self._process_component, comp)
                future_to_task[future] = (comp, lvl)
                stats["submitted"] += 1
                logger.debug("Submitted component='%s' at level=%d", comp.name, lvl)

            # 1. Initial Seeding
            for component, level in _component_expansion_seeds(root_components, self.depth_level):
                submit_component(component, level)

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
                            self._strip_ignored(analysis, sub_analyses)
                            save_analysis(
                                analysis=analysis,
                                output_dir=Path(self.output_dir),
                                sub_analyses=sub_analyses,
                                repo_name=self.repo_name,
                                repo_dir=self.repo_location,
                                source_tree_hash=self._source_tree_hash(),
                            )

                        if new_components and level + 1 < self.depth_level:
                            for child in new_components:
                                submit_component(child, level + 1)

                            logger.info("Expanded '%s' with %d new children.", comp_name, len(new_components))

                    except LLMAuthError:
                        # Rejected key: abort the whole run rather than logging one
                        # error per component and continuing with a dead key.
                        raise
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

    @track_analysis
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

            analysis_path = self.finalize_and_save(analysis, sub_analyses)
            logger.info(f"Analysis complete. Written unified analysis to {analysis_path}")
            return analysis_path

    def rebuild_global_relations(
        self,
        root_analysis: AnalysisInsights,
        sub_analyses: dict[str, AnalysisInsights],
    ) -> list:
        """Rebuild cross-boundary component relations at the deepest available granularity.

        Walks the full CFG with a global node->deepest-component-id map so we
        catch edges like ``1.1.1 -> 2.1.2`` that per-level analysis cannot see.
        Mutates ``root_analysis.components_relations`` in place.
        """
        if not self.static_analysis:
            return []
        cfg_graphs = {str(lang): self.static_analysis.get_cfg(lang) for lang in self.static_analysis.get_languages()}
        global_relations = build_global_relations(root_analysis, sub_analyses, cfg_graphs)
        root_analysis.components_relations = global_relations
        return global_relations

    def finalize_for_save(
        self,
        root_analysis: AnalysisInsights,
        sub_analyses: dict[str, AnalysisInsights],
    ) -> None:
        """Prepare an analysis tree for its authoritative save.

        Single pre-save chokepoint shared by the full, incremental, and partial
        flows. All steps are idempotent and
        safe with an empty ``sub_analyses`` (rebuild is a root-only pass).
        """
        self.rebuild_global_relations(root_analysis, sub_analyses)
        self._strip_ignored(root_analysis, sub_analyses)

    def finalize_and_save(
        self,
        root_analysis: AnalysisInsights,
        sub_analyses: dict[str, AnalysisInsights],
        *,
        seed_delta: dict[str, ClusterResult] | None = None,
        persist_side_artifacts: bool = True,
    ) -> Path:
        """Shared post-analysis tail for every flow: finalize, persist, return the path.

        ``finalize_for_save`` then ``save_analysis`` (stamped with the current
        ``source_tree_hash`` and file-coverage summary). ``seed_delta`` is the
        incremental-only cluster baseline, seeded *after* the save so a crash in
        between re-does the delta (idempotent) rather than silently skipping it.

        ``persist_side_artifacts`` writes ``file_coverage.json``, the static-
        analysis cache, and the ``fingerprint.json`` sidecar. The partial flow
        sets it False: it regenerates one component, not the source state, so
        rewriting those would drop the ``static_analysis.sha`` tag (cold-starting
        the next incremental) and desync the sidecar from ``source_tree_hash``.
        """
        self.finalize_for_save(root_analysis, sub_analyses)
        if persist_side_artifacts:
            source_tree_hash = self._source_tree_hash()
        else:
            # Partial: keep the prior hash so metadata matches the unrewritten sidecar.
            prior_metadata = load_analysis_metadata(Path(self.output_dir)) or {}
            source_tree_hash = prior_metadata.get("source_tree_hash", "") or self._source_tree_hash()
        analysis_path = save_analysis(
            analysis=root_analysis,
            output_dir=Path(self.output_dir),
            sub_analyses=sub_analyses,
            repo_name=self.repo_name,
            file_coverage_summary=self._build_file_coverage_summary(),
            repo_dir=self.repo_location,
            source_tree_hash=source_tree_hash,
        ).resolve()
        if seed_delta is not None:
            self._seed_incremental_cluster_cache(seed_delta)
        if persist_side_artifacts:
            self._write_file_coverage()
            self._persist_static_analysis_artifact()
            # Whole-tree sidecar (not the component-only files block) so the next
            # incremental diffs the same set source_tree_hash covers.
            write_fingerprint(Path(self.output_dir), self._source_tree_fingerprint_map())
        return analysis_path

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

    def _apply_incremental_scope_recursively(
        self,
        scope_id: str,
        scope: AnalysisInsights,
        structural_diff: StructuralClusterDiff,
        cluster_results: dict[str, ClusterResult],
        sub_analyses: dict[str, AnalysisInsights],
    ) -> RecursiveScopeUpdateResult:
        assert self.incremental_planning_agent is not None
        assert self.incremental_agent is not None
        decision = self.incremental_planning_agent.decide_scope_update(
            scope_id,
            scope,
            structural_diff,
            cluster_results,
        )
        apply_result = self.incremental_agent.update_scope(scope_id, scope, decision, cluster_results)
        result = RecursiveScopeUpdateResult(
            refresh_ids=set(apply_result.refresh_ids),
            new_component_ids=set(apply_result.new_component_ids),
            removed_ids=set(apply_result.removed_ids),
        )
        if apply_result.refresh_ids or apply_result.removed_ids:
            result.relation_contexts[scope_id] = apply_result.relation_context

        components_by_id = {
            component.component_id: component for component in scope.components if component.component_id
        }
        existing_refresh_ids = apply_result.refresh_ids - apply_result.new_component_ids
        for component_id in sorted(existing_refresh_ids):
            child_scope = sub_analyses.get(component_id)
            child_component = components_by_id.get(component_id)
            if child_scope is None or child_component is None or _component_depth(component_id) >= self.depth_level:
                continue
            child_cluster_results, child_diff = _build_scope_incremental_inputs(
                child_component,
                component_id,
                self.incremental_agent,
                self.changes,
                self.repo_location,
            )
            if not child_diff.has_changes:
                continue
            if not _child_scope_needs_recursive_update(child_scope, child_diff):
                continue
            child_result = self._apply_incremental_scope_recursively(
                component_id,
                child_scope,
                child_diff,
                child_cluster_results,
                sub_analyses,
            )
            result.refresh_ids |= child_result.refresh_ids
            result.new_component_ids |= child_result.new_component_ids
            result.removed_ids |= child_result.removed_ids
            result.relation_contexts.update(child_result.relation_contexts)
        return result

    @track_analysis
    def generate_analysis_incremental(
        self,
        root_analysis: AnalysisInsights,
        sub_analyses: dict[str, AnalysisInsights],
    ) -> Path:
        """Cluster-driven incremental update of an existing ``analysis.json``.

        Deterministic cluster delta, one LLM call to route delta clusters,
        then ``_generate_subcomponents`` seeded with the changed components.
        Raises when no trustworthy baseline or scoped update plan is available.
        """
        if self.details_agent is None or self.incremental_planning_agent is None or self.incremental_agent is None:
            self.pre_analysis()
        assert self.static_analysis is not None
        assert self.details_agent is not None
        assert self.incremental_planning_agent is not None
        assert self.incremental_agent is not None

        monitor = self.stats_writer if self.stats_writer else nullcontext()
        with monitor:
            # Scrub before cluster math: orphan-routed files never appear in
            # any cluster, so deletes wouldn't surface via the delta alone.
            live_files: set[str] = set()
            for language in self.static_analysis.get_languages():
                try:
                    cfg = self.static_analysis.get_cfg(language)
                except (ValueError, KeyError):
                    continue
                for node in cfg.nodes.values():
                    if node.file_path:
                        live_files.add(normalize_repo_path(node.file_path, self.repo_location))
            remove_deleted_files(root_analysis, sub_analyses, live_files)

            snapshot_source = self.static_analysis.incremental_base_results or self.static_analysis
            old_snapshot = snapshot_from_static_analysis(snapshot_source)
            if not old_snapshot.all_cluster_ids():
                # No cluster_cache on the live CFG — no prior pkl, legacy pkl,
                # or first-ever incremental run. Refuse to silently rebuild
                # from scratch; that would discard the existing analysis.json's
                # depth and component IDs. Caller must explicitly request a
                # full run instead.  ``IncrementalCacheMissingError`` inspects
                # the artifact dir to pick the specific diagnostic (missing
                # pkl, missing sha, or pkl-without-cluster-baseline).
                artifact_dir = self.output_dir
                error = IncrementalCacheMissingError(artifact_dir)
                logger.error("%s", error)
                raise error

            delta = compute_cluster_delta(
                old_snapshot,
                self.static_analysis,
                changes=self.changes,
                repo_dir=self.repo_location,
            )
            if not delta.has_changes:
                logger.info("Cluster delta is empty; rewriting current analysis without re-detailing.")
                # No structural change, but a body-only edit still moves content
                # hashes — refresh the files index from live source so they don't
                # go stale (relations are already the global set here).
                self._refresh_files_index(root_analysis, sub_analyses)
                return self.finalize_and_save(root_analysis, sub_analyses)

            structural_diff = structural_diff_from_delta(
                old_snapshot,
                delta,
                changes=self.changes,
                repo_dir=self.repo_location,
            )
            protected_empty_ids = _cluster_backed_empty_component_ids(root_analysis, sub_analyses)
            apply_result = self._apply_incremental_scope_recursively(
                ROOT_SCOPE_ID,
                root_analysis,
                structural_diff,
                delta.cluster_results(),
                sub_analyses,
            )

            removed_ids = prune_empty_components(root_analysis, sub_analyses, protected_empty_ids)
            if removed_ids:
                apply_result.refresh_ids -= removed_ids
                apply_result.new_component_ids -= removed_ids
            _drop_removed_subtree_analyses(sub_analyses, apply_result.removed_ids | removed_ids)

            new_components = [
                component
                for component in _collect_components_by_id(apply_result.new_component_ids, root_analysis, sub_analyses)
                if _component_depth(component.component_id) < self.depth_level
            ]
            if new_components:
                _, redetailed_subs = self._generate_subcomponents(root_analysis, new_components)
                _merge_sub_analyses(sub_analyses, redetailed_subs)

            if apply_result.relation_contexts:
                self.incremental_agent.generate_all_scope_relations(
                    root_analysis,
                    sub_analyses,
                    apply_result.relation_contexts,
                )

            self._refresh_files_index(root_analysis, sub_analyses)

            analysis_path = self.finalize_and_save(root_analysis, sub_analyses, seed_delta=delta.cluster_results())
            n_subs = sum(len(sub.components) for sub in sub_analyses.values())
            logger.info(
                "[incremental] saved: %d root + %d sub-components, %d relations",
                len(root_analysis.components),
                n_subs,
                len(root_analysis.components_relations),
            )
            return analysis_path

    def _refresh_files_index(
        self,
        root_analysis: AnalysisInsights,
        sub_analyses: dict[str, AnalysisInsights],
    ) -> None:
        """Rebuild live per-scope file indexes and union them into the root index."""
        assert self.static_analysis is not None
        analyses = (root_analysis, *sub_analyses.values())
        source_cache: SourceCache = {}
        for analysis in analyses:
            refresh_method_spans_from_cfg(analysis, self.static_analysis, self.repo_location)
            analysis.files = build_files_index(analysis, self.repo_location, source_cache)
            index_relation_endpoints(analysis, self.repo_location)

        unified_files: dict[str, FileEntry] = {}
        for analysis in analyses:
            for fp, entry in analysis.files.items():
                unified_files.setdefault(fp, FileEntry()).merge_from(entry)
        root_analysis.files = unified_files


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


def _drop_removed_subtree_analyses(sub_analyses: dict[str, AnalysisInsights], removed_ids: set[str]) -> None:
    for removed_id in removed_ids:
        for scope_id in list(sub_analyses):
            if is_self_or_descendant(scope_id, removed_id):
                del sub_analyses[scope_id]


def _cluster_backed_empty_component_ids(
    root_analysis: AnalysisInsights,
    sub_analyses: dict[str, AnalysisInsights],
) -> set[str]:
    protected_ids: set[str] = set()
    for analysis in [root_analysis, *sub_analyses.values()]:
        for component in analysis.components:
            if (
                component.component_id
                and component.source_cluster_ids
                and not component.key_entities
                and not any(group.methods for group in component.file_methods)
            ):
                protected_ids.add(component.component_id)
    return protected_ids


def _child_scope_needs_recursive_update(
    child_scope: AnalysisInsights,
    structural_diff: StructuralClusterDiff,
) -> bool:
    owned_qnames = {
        method.qualified_name
        for component in child_scope.components
        for group in component.file_methods
        for method in group.methods
        if method.qualified_name
    }
    removed_qnames: set[str] = set()
    for diff in structural_diff.by_language.values():
        for member_delta in [*diff.modified, *diff.new_details]:
            removed_qnames.update(member_delta.removed_methods)
    return bool(removed_qnames.intersection(owned_qnames))


def _build_scope_incremental_inputs(
    component: Component,
    scope_id: str,
    incremental_agent: IncrementalAgent,
    changes: ChangeSet | None,
    repo_dir: Path,
) -> tuple[dict[str, ClusterResult], StructuralClusterDiff]:
    old_snapshot = scoped_snapshot_for_component(component, scope_id, incremental_agent)
    if not old_snapshot.all_cluster_ids():
        return {}, StructuralClusterDiff()

    _subgraph_str, cluster_results, _subgraph_cfgs = incremental_agent._create_strict_component_subgraph(
        component,
        source_cluster_id_prefix=scope_id,
    )
    delta = ClusterDelta(
        by_language={
            language: LanguageDelta(language=language, cluster_results=cluster_result)
            for language, cluster_result in cluster_results.items()
        }
    )
    structural_diff = structural_diff_from_delta(
        old_snapshot,
        delta,
        changes=changes,
        repo_dir=repo_dir,
        scope_id=scope_id,
    )
    return cluster_results, structural_diff


def scoped_snapshot_for_component(
    component: Component,
    scope_id: str,
    incremental_agent: IncrementalAgent,
) -> ClusterSnapshot:
    assigned_qnames = {
        method.qualified_name for group in component.file_methods for method in group.methods if method.qualified_name
    }
    by_language = {}
    for language in incremental_agent.static_analysis.get_languages():
        cfg = incremental_agent.static_analysis.get_cfg(language)
        sub_cfg = cfg.filter_by_nodes(assigned_qnames)
        if sub_cfg.nodes:
            by_language[str(language)] = scoped_snapshot_from_lineage(sub_cfg, scope_id)
    return ClusterSnapshot(by_language=by_language)


def _merge_sub_analyses(
    target: dict[str, AnalysisInsights],
    updates: dict[str, AnalysisInsights],
) -> None:
    """Merge *updates* into *target*, preserving components the redetailer didn't touch.

    ``_generate_subcomponents`` produces fresh sub-analyses that only contain
    components the detailer LLM generated. In the incremental path, scoped
    operations may have inserted brand-new components that the detailer never
    saw because they weren't in its input scope. A plain ``dict.update()``
    would wipe those survivors out.

    For each key in *updates*, we:
      1. Keep old components whose IDs are absent from the new sub-analysis.
      2. Replace everything else with the new sub-analysis data.

    Relations are not merged here: they live once on the root as the global set
    and are rebuilt wholesale by ``rebuild_global_relations`` after this merge.
    """
    for key, new_sub in updates.items():
        old_sub = target.get(key)
        if old_sub is None:
            target[key] = new_sub
            continue

        new_ids = {c.component_id for c in new_sub.components}
        surviving = [c for c in old_sub.components if c.component_id not in new_ids]
        if surviving:
            new_sub.components = surviving + new_sub.components

        target[key] = new_sub
