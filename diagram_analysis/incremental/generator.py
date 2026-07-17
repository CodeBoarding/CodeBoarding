"""Transactional entry point for recursive incremental analysis."""

from collections import Counter
import copy
import logging
from pathlib import Path

from filelock import FileLock

from agents.analysis_result_responses import AnalysisInsights
from agents.incremental_agent import IncrementalAgent
from diagram_analysis.diagram_generator import DiagramGenerator
from diagram_analysis.incremental.errors import IncrementalAnalysisError
from diagram_analysis.incremental.processor import IncrementalScopeProcessor
from diagram_analysis.incremental.state import INCREMENTAL_STATE_FILENAME, IncrementalIdState
from diagram_analysis.io_utils import write_text_atomic
from diagram_analysis.run_context import RunContext, RunPaths
from static_analyzer import StaticAnalyzer
from static_analyzer.analysis_cache import STATIC_ANALYSIS_PKL, STATIC_ANALYSIS_SHA, copy_cache_files
from static_analyzer.analysis_result import StaticAnalysisResults
from utils import ANALYSIS_FILENAME, FINGERPRINT_FILENAME, generate_run_id

logger = logging.getLogger(__name__)

PROMOTED_TEXT_ARTIFACTS = (
    FINGERPRINT_FILENAME,
    "file_coverage.json",
    INCREMENTAL_STATE_FILENAME,
    ANALYSIS_FILENAME,
)


class IncrementalGenerator:
    """Run an incremental candidate in isolation and promote it on success."""

    def __init__(
        self,
        run_paths: RunPaths,
        run_context: RunContext,
        depth_level: int,
        previous_static: StaticAnalysisResults,
        root_analysis: AnalysisInsights,
        sub_analyses: dict[str, AnalysisInsights],
        monitoring_enabled: bool,
        static_analyzer: StaticAnalyzer | None,
        source_sha: str | None,
    ) -> None:
        self.run_paths = run_paths
        self.run_context = run_context
        self.depth_level = depth_level
        self.previous_static = previous_static
        self.root_analysis = root_analysis
        self.sub_analyses = sub_analyses
        self.monitoring_enabled = monitoring_enabled
        self.static_analyzer = static_analyzer
        self.source_sha = source_sha
        self.run_dir = run_paths.output_dir / "runs" / f"incremental-{generate_run_id()}"
        self.baseline_dir = self.run_dir / "baseline"
        self.candidate_dir = self.run_dir / "candidate"

    def run(self) -> Path:
        self._prepare_run()
        baseline_root = copy.deepcopy(self.root_analysis)
        baseline_sub_analyses = copy.deepcopy(self.sub_analyses)
        generator = DiagramGenerator(
            repo_location=self.run_paths.repo_path,
            temp_folder=self.candidate_dir,
            repo_name=self.run_paths.project_name,
            output_dir=self.candidate_dir,
            depth_level=self.depth_level,
            run_id=self.run_context.run_id,
            log_path=self.run_context.log_path,
            monitoring_enabled=self.monitoring_enabled,
            static_analyzer=self.static_analyzer,
        )
        generator.source_sha = self.source_sha
        generator.pre_analysis()
        if generator.details_agent is None or generator.static_analysis is None:
            raise IncrementalAnalysisError("Incremental analysis dependencies were not initialized")

        agent = IncrementalAgent(
            repo_dir=self.run_paths.repo_path,
            static_analysis=generator.static_analysis,
            agent_llm=generator.details_agent.agent_llm,
            parsing_llm=generator.details_agent.parsing_llm,
        )
        id_state = IncrementalIdState.load(self.run_paths.output_dir)
        processor = IncrementalScopeProcessor(
            generator,
            agent,
            self.previous_static,
            self.root_analysis,
            self.sub_analyses,
            id_state,
        )
        self._validate_cluster_lineage(generator.static_analysis)
        root_analysis, sub_analyses = processor.run()
        id_state.save(self.candidate_dir)
        candidate_path = generator.finalize_and_save(root_analysis, sub_analyses)
        self._validate_architecture(
            root_analysis,
            sub_analyses,
            baseline_root,
            baseline_sub_analyses,
            processor.changed_component_ids,
            processor.removed_component_ids,
        )
        self._promote()
        logger.info("Promoted successful incremental run from %s", self.run_dir)
        return self.run_paths.output_dir / candidate_path.name

    def _prepare_run(self) -> None:
        self.baseline_dir.mkdir(parents=True, exist_ok=False)
        self.candidate_dir.mkdir(parents=True, exist_ok=False)
        if not copy_cache_files(self.run_paths.output_dir, self.baseline_dir):
            raise IncrementalAnalysisError("Could not stage the baseline static-analysis artifact")
        if not copy_cache_files(self.run_paths.output_dir, self.candidate_dir):
            raise IncrementalAnalysisError("Could not create the candidate static-analysis artifact")
        baseline_analysis = self.run_paths.output_dir / ANALYSIS_FILENAME
        if not baseline_analysis.is_file():
            raise IncrementalAnalysisError("Could not stage the baseline analysis artifact")
        write_text_atomic(
            self.baseline_dir / ANALYSIS_FILENAME,
            baseline_analysis.read_text(encoding="utf-8"),
        )

    def _validate_cluster_lineage(self, current_static: StaticAnalysisResults) -> None:
        retained = 0
        shared = 0
        current_graphs = current_static.available_program_graphs()
        for language, previous_graph in self.previous_static.available_program_graphs().items():
            current_graph = current_graphs.get(language)
            if (
                current_graph is None
                or previous_graph.cluster_snapshot is None
                or current_graph.cluster_snapshot is None
            ):
                continue
            previous_owner = {
                node_id: cluster_id
                for cluster_id, members in previous_graph.cluster_snapshot.cluster_result.clusters.items()
                for node_id in members
            }
            current_owner = {
                node_id: cluster_id
                for cluster_id, members in current_graph.cluster_snapshot.cluster_result.clusters.items()
                for node_id in members
            }
            shared_nodes = set(previous_owner) & set(current_owner)
            shared += len(shared_nodes)
            retained += sum(previous_owner[node_id] == current_owner[node_id] for node_id in shared_nodes)
        if shared >= 20 and retained / shared < 0.8:
            raise IncrementalAnalysisError(
                f"Incremental cluster lineage retained only {retained}/{shared} shared symbols"
            )

    @staticmethod
    def _validate_architecture(
        root_analysis: AnalysisInsights,
        sub_analyses: dict[str, AnalysisInsights],
        baseline_root: AnalysisInsights,
        baseline_sub_analyses: dict[str, AnalysisInsights],
        changed_component_ids: set[str],
        removed_component_ids: set[str],
    ) -> None:
        IncrementalGenerator._validate_frozen_components(
            baseline_root,
            baseline_sub_analyses,
            root_analysis,
            sub_analyses,
            changed_component_ids,
            removed_component_ids,
        )
        baseline_scopes = {"root": baseline_root, **baseline_sub_analyses}
        all_component_ids = [
            component.component_id
            for candidate_analysis in [root_analysis, *sub_analyses.values()]
            for component in candidate_analysis.components
        ]
        if len(all_component_ids) != len(set(all_component_ids)):
            raise IncrementalAnalysisError("Incremental candidate contains duplicate component IDs")
        all_live_ids = set(all_component_ids)
        for scope_id, analysis in [("root", root_analysis), *sorted(sub_analyses.items())]:
            IncrementalGenerator._validate_scope_quality(
                scope_id,
                analysis,
                baseline_scopes.get(scope_id),
                all_live_ids,
            )

    @staticmethod
    def _validate_scope_quality(
        scope_id: str,
        analysis: AnalysisInsights,
        baseline: AnalysisInsights | None,
        all_live_ids: set[str],
    ) -> None:
        component_ids = [component.component_id for component in analysis.components]
        if len(component_ids) != len(set(component_ids)):
            raise IncrementalAnalysisError(f"Incremental scope {scope_id!r} contains duplicate component IDs")
        names = [component.name.strip().casefold() for component in analysis.components]
        duplicates = sorted(name for name, count in Counter(names).items() if name and count > 1)
        if duplicates:
            raise IncrementalAnalysisError(
                f"Incremental scope {scope_id!r} contains duplicate component names: {duplicates}"
            )
        component_count = len(analysis.components)
        if component_count > 100 or (baseline is not None and component_count > max(10, len(baseline.components) * 3)):
            baseline_count = len(baseline.components) if baseline is not None else 0
            raise IncrementalAnalysisError(
                f"Incremental scope {scope_id!r} expanded from {baseline_count} to {component_count} components"
            )

        cluster_assignments = [
            cluster_id for component in analysis.components for cluster_id in component.source_cluster_ids
        ]
        duplicate_clusters = sorted(
            cluster_id for cluster_id, count in Counter(cluster_assignments).items() if count > 1
        )
        if duplicate_clusters:
            raise IncrementalAnalysisError(
                f"Incremental scope {scope_id!r} assigns modules to multiple components: {duplicate_clusters}"
            )
        singleton_count = sum(
            sum(len(group.methods) for group in component.file_methods) <= 1 for component in analysis.components
        )
        if component_count >= 20 and singleton_count / component_count > 0.8:
            raise IncrementalAnalysisError(
                f"Incremental scope {scope_id!r} contains {singleton_count} singleton components"
            )

        method_names = [
            method.qualified_name
            for component in analysis.components
            for file_group in component.file_methods
            for method in file_group.methods
        ]
        duplicate_methods = sorted(name for name, count in Counter(method_names).items() if count > 1)
        if duplicate_methods:
            raise IncrementalAnalysisError(
                f"Incremental scope {scope_id!r} assigns methods to multiple components: {duplicate_methods[:20]}"
            )

        live_ids = all_live_ids if scope_id == "root" else set(component_ids)
        invalid_relations = [
            (relation.src_id, relation.dst_id)
            for relation in analysis.components_relations
            if relation.src_id not in live_ids or relation.dst_id not in live_ids or relation.src_id == relation.dst_id
        ]
        if invalid_relations:
            raise IncrementalAnalysisError(
                f"Incremental scope {scope_id!r} contains invalid relation endpoints: {invalid_relations[:20]}"
            )

    @staticmethod
    def _validate_frozen_components(
        baseline_root: AnalysisInsights,
        baseline_sub_analyses: dict[str, AnalysisInsights],
        current_root: AnalysisInsights,
        current_sub_analyses: dict[str, AnalysisInsights],
        changed_component_ids: set[str],
        removed_component_ids: set[str],
    ) -> None:
        baseline_components = {
            component.component_id: component
            for analysis in [baseline_root, *baseline_sub_analyses.values()]
            for component in analysis.components
        }
        current_components = {
            component.component_id: component
            for analysis in [current_root, *current_sub_analyses.values()]
            for component in analysis.components
        }

        def removed(component_id: str) -> bool:
            return any(
                component_id == removed_id or component_id.startswith(f"{removed_id}.")
                for removed_id in removed_component_ids
            )

        for component_id, baseline in baseline_components.items():
            if component_id in changed_component_ids or removed(component_id):
                continue
            current = current_components.get(component_id)
            if current is None:
                raise IncrementalAnalysisError(
                    f"Untouched component {component_id!r} disappeared from the incremental candidate"
                )
            baseline_semantics = (
                baseline.name,
                baseline.description,
                tuple(baseline.source_cluster_ids),
                tuple(reference.qualified_name for reference in baseline.key_entities),
            )
            current_semantics = (
                current.name,
                current.description,
                tuple(current.source_cluster_ids),
                tuple(reference.qualified_name for reference in current.key_entities),
            )
            if current_semantics != baseline_semantics:
                raise IncrementalAnalysisError(
                    f"Untouched component {component_id!r} changed in the incremental candidate"
                )

    def _promote(self) -> None:
        required = [
            self.candidate_dir / ANALYSIS_FILENAME,
            self.candidate_dir / FINGERPRINT_FILENAME,
            self.candidate_dir / STATIC_ANALYSIS_PKL,
            self.candidate_dir / STATIC_ANALYSIS_SHA,
        ]
        missing = [path.name for path in required if not path.is_file()]
        if missing:
            raise IncrementalAnalysisError(f"Incremental candidate is missing artifacts: {missing}")

        output_dir = self.run_paths.output_dir
        previous_text = {
            name: (output_dir / name).read_text(encoding="utf-8") if (output_dir / name).is_file() else None
            for name in PROMOTED_TEXT_ARTIFACTS
        }
        with FileLock(output_dir / f"{ANALYSIS_FILENAME}.lock", timeout=30):
            try:
                for name in PROMOTED_TEXT_ARTIFACTS[:-1]:
                    candidate = self.candidate_dir / name
                    if candidate.is_file():
                        write_text_atomic(output_dir / name, candidate.read_text(encoding="utf-8"))
                if not copy_cache_files(self.candidate_dir, output_dir):
                    raise IncrementalAnalysisError("Could not promote the candidate static-analysis artifact")
                write_text_atomic(
                    output_dir / ANALYSIS_FILENAME,
                    (self.candidate_dir / ANALYSIS_FILENAME).read_text(encoding="utf-8"),
                )
            except Exception:
                self._restore_text(previous_text)
                if not copy_cache_files(self.baseline_dir, output_dir):
                    logger.exception("Failed to restore the baseline static-analysis artifact")
                raise

    def _restore_text(self, previous_text: dict[str, str | None]) -> None:
        for name, content in previous_text.items():
            path = self.run_paths.output_dir / name
            if content is None:
                path.unlink(missing_ok=True)
            else:
                write_text_atomic(path, content)
