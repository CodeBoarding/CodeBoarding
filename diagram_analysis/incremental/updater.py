"""Recursive method-scoped architecture updates."""

from __future__ import annotations

from collections import defaultdict
import logging
from pathlib import Path

import networkx as nx

from agents.analysis_result_responses import AnalysisInsights, Component
from agents.cluster_ids import CodeBoardingClusterIds
from agents.content_hash import SourceCache, hash_method_body, read_source_lines
from agents.file_index_models import FileMethodGroup, MethodEntry
from agents.incremental_agent import IncrementalAgent, IncrementalArchitectureContext
from agents.incremental_responses import IncrementalComponentDraft
from diagram_analysis.diagram_generator import DiagramGenerator
from diagram_analysis.incremental.contracts import IncrementalContractsUpdater
from diagram_analysis.incremental.errors import IncrementalAnalysisError
from diagram_analysis.incremental.models import MethodDelta, MethodRecord, ScopeGraph
from diagram_analysis.incremental.state import IncrementalIdState
from repo_utils.path_utils import normalize_repo_path
from static_analyzer.analysis_result import StaticAnalysisResults
from static_analyzer.cluster_helpers import build_all_cluster_results, reindex_cross_language_clusters
from static_analyzer.clustering import ClusterResult
from static_analyzer.constants import CALLABLE_TYPES, CLASS_TYPES
from static_analyzer.program_graph import ProgramGraph

logger = logging.getLogger(__name__)


class IncrementalAnalysisUpdater:
    """Patch affected scopes and expand only newly created branches."""

    def __init__(
        self,
        generator: DiagramGenerator,
        incremental_agent: IncrementalAgent,
        previous_static: StaticAnalysisResults,
        root_analysis: AnalysisInsights,
        sub_analyses: dict[str, AnalysisInsights],
    ) -> None:
        if generator.static_analysis is None or generator.details_agent is None:
            raise IncrementalAnalysisError("Incremental generator has not completed pre-analysis")
        self.generator = generator
        self.agent = incremental_agent
        self.previous_static = previous_static
        self.current_static = generator.static_analysis
        self.root_analysis = root_analysis
        self.sub_analyses = sub_analyses
        self.id_state = IncrementalIdState.load(Path(generator.output_dir))
        self.contracts = IncrementalContractsUpdater(incremental_agent, self.current_static)
        self.old_methods = self._old_method_records(root_analysis)
        self.current_methods = self._current_method_records()
        self.old_root = self._root_scope_from_snapshots(previous_static)
        self.current_root = self._root_scope_current()
        self.delta = self._build_method_delta()

    def run(self) -> tuple[AnalysisInsights, dict[str, AnalysisInsights]]:
        if self.delta.architecture_methods:
            self._update_scope(
                analysis=self.root_analysis,
                parent=None,
                old_scope=self.old_root,
                current_scope=self.current_root,
            )
        self._refresh_metadata()
        self.id_state.save(Path(self.generator.output_dir))
        return self.root_analysis, self.sub_analyses

    def _old_method_records(self, analysis: AnalysisInsights) -> dict[str, MethodRecord]:
        records: dict[str, MethodRecord] = {}
        for file_path, file_entry in analysis.files.items():
            for method in file_entry.methods:
                if not method.content_hash:
                    raise IncrementalAnalysisError(
                        f"Baseline method {method.qualified_name!r} has no content hash; run a full analysis first"
                    )
                records[method.qualified_name] = MethodRecord(
                    qualified_name=method.qualified_name,
                    file_path=file_path,
                    start_line=method.start_line,
                    end_line=method.end_line,
                    node_type=method.node_type,
                    content_hash=method.content_hash,
                )
        if not records:
            raise IncrementalAnalysisError("Baseline analysis has no persisted method index")
        return records

    def _current_method_records(self) -> dict[str, MethodRecord]:
        records: dict[str, MethodRecord] = {}
        source_cache: SourceCache = {}
        allowed_types = CALLABLE_TYPES | CLASS_TYPES
        for graph in self.current_static.available_program_graphs().values():
            for node in graph.symbol_nodes():
                if node.symbol_type not in allowed_types or node.symbol_type is None:
                    continue
                file_path = normalize_repo_path(node.file_path, self.generator.repo_location)
                content_hash = hash_method_body(
                    read_source_lines(self.generator.repo_location, file_path, source_cache),
                    node.line_start,
                    node.line_end,
                )
                if not content_hash:
                    raise IncrementalAnalysisError(
                        f"Current method {node.id!r} could not be content-hashed; incremental state is untrustworthy"
                    )
                records[node.id] = MethodRecord(
                    qualified_name=node.id,
                    file_path=file_path,
                    start_line=node.line_start,
                    end_line=node.line_end,
                    node_type=node.symbol_type.name,
                    content_hash=content_hash,
                )
        if not records:
            raise IncrementalAnalysisError("Current static analysis contains no methods")
        return records

    def _root_scope_from_snapshots(self, static_analysis: StaticAnalysisResults) -> ScopeGraph:
        cluster_results: dict[str, ClusterResult] = {}
        graphs = static_analysis.available_program_graphs()
        for language, graph in graphs.items():
            if graph.cluster_snapshot is None:
                raise IncrementalAnalysisError(f"Missing Infomap lineage for {language}; run a full analysis first")
            cluster_results[str(language)] = graph.cluster_snapshot.cluster_result
        if len(cluster_results) > 1:
            cluster_results = {
                language: self._copy_cluster_result(result) for language, result in cluster_results.items()
            }
            reindex_cross_language_clusters(cluster_results)
        return self._scope_graph(cluster_results, graphs, "")

    def _root_scope_current(self) -> ScopeGraph:
        cluster_results = build_all_cluster_results(self.current_static)
        return self._scope_graph(cluster_results, self.current_static.available_program_graphs(), "")

    @staticmethod
    def _copy_cluster_result(result: ClusterResult) -> ClusterResult:
        return ClusterResult(
            clusters={cluster_id: set(members) for cluster_id, members in result.clusters.items()},
            cluster_to_files={cluster_id: set(paths) for cluster_id, paths in result.cluster_to_files.items()},
            file_to_clusters={path: set(cluster_ids) for path, cluster_ids in result.file_to_clusters.items()},
            strategy=result.strategy,
        )

    @staticmethod
    def _scope_graph(
        cluster_results: dict[str, ClusterResult],
        graphs: dict[str, ProgramGraph],
        prefix: str,
    ) -> ScopeGraph:
        members: dict[str, set[str]] = {}
        method_to_cluster: dict[str, str] = {}
        for result in cluster_results.values():
            for local_id, cluster_members in result.clusters.items():
                cluster_id = CodeBoardingClusterIds.qualify_local_id(str(local_id), prefix)
                members[cluster_id] = set(cluster_members)
                for qualified_name in cluster_members:
                    if qualified_name in method_to_cluster:
                        raise IncrementalAnalysisError(f"Method {qualified_name!r} belongs to multiple scoped clusters")
                    method_to_cluster[qualified_name] = cluster_id
        return ScopeGraph(cluster_results, graphs, members, method_to_cluster)

    def _build_method_delta(self) -> MethodDelta:
        old_names = set(self.old_methods)
        current_names = set(self.current_methods)
        shared = old_names & current_names
        modified = {
            name for name in shared if self.old_methods[name].content_hash != self.current_methods[name].content_hash
        }
        metadata_only = {
            name
            for name in shared - modified
            if (
                self.old_methods[name].file_path,
                self.old_methods[name].start_line,
                self.old_methods[name].end_line,
            )
            != (
                self.current_methods[name].file_path,
                self.current_methods[name].start_line,
                self.current_methods[name].end_line,
            )
        }
        cluster_reassigned = {
            name
            for name in shared
            if self.old_root.method_to_cluster.get(name) != self.current_root.method_to_cluster.get(name)
        }
        old_calls = self._call_topology(self.previous_static)
        current_calls = self._call_topology(self.current_static)
        added_calls = current_calls - old_calls
        deleted_calls = old_calls - current_calls
        call_boundary_changed = {endpoint for edge in added_calls | deleted_calls for endpoint in edge}
        delta = MethodDelta(
            added=current_names - old_names,
            deleted=old_names - current_names,
            modified=modified,
            cluster_reassigned=cluster_reassigned,
            call_boundary_changed=call_boundary_changed,
            metadata_only=metadata_only,
            added_calls=added_calls,
            deleted_calls=deleted_calls,
        )
        logger.info(
            "Method delta: added=%d deleted=%d modified=%d reassigned=%d call-boundary=%d metadata-only=%d",
            len(delta.added),
            len(delta.deleted),
            len(delta.modified),
            len(delta.cluster_reassigned),
            len(delta.call_boundary_changed),
            len(delta.metadata_only),
        )
        return delta

    @staticmethod
    def _call_topology(static_analysis: StaticAnalysisResults) -> set[tuple[str, str]]:
        return {
            (edge.source, edge.target)
            for graph in static_analysis.available_program_graphs().values()
            for edge in graph.call_edges()
        }

    def _update_scope(
        self,
        analysis: AnalysisInsights,
        parent: Component | None,
        old_scope: ScopeGraph,
        current_scope: ScopeGraph,
    ) -> None:
        parent_id = parent.component_id if parent is not None else ""
        old_component_methods = {
            component.component_id: self._component_method_names(component) for component in analysis.components
        }
        old_method_owner = {
            method: component_id for component_id, methods in old_component_methods.items() for method in methods
        }
        scope_methods = set(old_method_owner) | set(current_scope.method_to_cluster)
        affected_methods = self.delta.architecture_methods & scope_methods
        affected_methods.update(
            name
            for name in scope_methods
            if old_scope.method_to_cluster.get(name) != current_scope.method_to_cluster.get(name)
        )
        if not affected_methods:
            return

        old_cluster_owner = {
            cluster_id: component.component_id
            for component in analysis.components
            for cluster_id in component.source_cluster_ids
        }
        mutable_component_ids = {
            owner for name in affected_methods if (owner := old_method_owner.get(name)) is not None
        }
        mutable_component_ids.update(
            old_cluster_owner[cluster_id]
            for name in affected_methods
            if (cluster_id := current_scope.method_to_cluster.get(name)) in old_cluster_owner
        )
        affected_current_clusters = {
            current_scope.method_to_cluster[name]
            for name in affected_methods
            if name in current_scope.method_to_cluster
        }
        new_clusters = set(current_scope.cluster_members) - set(old_cluster_owner)
        affected_current_clusters.update(
            cluster_id for cluster_id in new_clusters if current_scope.cluster_members[cluster_id] & affected_methods
        )
        mutable_clusters = set(affected_current_clusters)
        mutable_clusters.update(
            cluster_id
            for cluster_id, owner in old_cluster_owner.items()
            if owner in mutable_component_ids and cluster_id in current_scope.cluster_members
        )

        mutable_components = [
            component for component in analysis.components if component.component_id in mutable_component_ids
        ]
        immutable_components = [
            component for component in analysis.components if component.component_id not in mutable_component_ids
        ]
        context = IncrementalArchitectureContext(
            parent=self._parent_context(parent, analysis),
            existing_components=self._component_context(analysis.components),
            immutable_components=self._component_context(immutable_components),
            mutable_clusters=self._cluster_context(current_scope, mutable_clusters, affected_methods),
            method_changes=self._method_change_context(affected_methods),
            call_changes=self._call_change_context(affected_methods),
            expected_cluster_ids=mutable_clusters,
            cluster_members={
                cluster_id: set(current_scope.cluster_members[cluster_id]) for cluster_id in mutable_clusters
            },
        )
        patch = self.agent.update_architecture(context)
        replacement, new_ids = self._reconcile_components(
            parent_id,
            patch.components,
            mutable_components,
            immutable_components,
            current_scope,
        )
        deleted_ids = mutable_component_ids - {component.component_id for component in replacement}
        for component_id in deleted_ids:
            self._remove_subtree(component_id)
        analysis.description = patch.description
        analysis.components = self._sort_components([*immutable_components, *replacement])
        changed_ids = {component.component_id for component in replacement}
        self.contracts.update(analysis, current_scope.graphs, changed_ids, deleted_ids)

        old_by_id = {component.component_id: component for component in mutable_components}
        for component in replacement:
            if self._component_depth(component.component_id) >= self.generator.depth_level:
                continue
            if component.component_id in new_ids:
                self._expand_new_branch(component)
                continue
            if component.component_id not in self.sub_analyses:
                self._expand_new_branch(component)
                continue
            old_component = old_by_id.get(component.component_id)
            if old_component is None:
                self._expand_new_branch(component)
                continue
            old_child_scope = self._old_child_scope(old_component)
            current_child_scope = self._current_child_scope(component)
            self._update_scope(
                self.sub_analyses[component.component_id],
                component,
                old_child_scope,
                current_child_scope,
            )

    def _old_child_scope(self, component: Component) -> ScopeGraph:
        method_names = self._component_method_names(component)
        method_to_cluster: dict[str, str] = {}
        members: dict[str, set[str]] = defaultdict(set)
        prefix = f"{component.component_id}."
        for graph in self.previous_static.available_program_graphs().values():
            for qualified_name, paths in graph.method_cluster_paths_snapshot():
                if qualified_name not in method_names:
                    continue
                scoped = sorted(
                    cluster_id
                    for cluster_id in paths
                    if cluster_id.startswith(prefix) and cluster_id.removeprefix(prefix).isdigit()
                )
                if scoped:
                    method_to_cluster[qualified_name] = scoped[0]
                    members[scoped[0]].add(qualified_name)
        if method_names and not method_to_cluster:
            raise IncrementalAnalysisError(
                f"Missing scoped cluster lineage for expanded component {component.component_id!r}"
            )
        return ScopeGraph({}, {}, dict(members), method_to_cluster)

    def _current_child_scope(self, component: Component) -> ScopeGraph:
        details_agent = self.generator.details_agent
        if details_agent is None:
            raise IncrementalAnalysisError("DetailsAgent is unavailable")
        _, cluster_results, graphs = details_agent._create_strict_component_subgraph(
            component,
            source_cluster_id_prefix=component.component_id,
        )
        return self._scope_graph(cluster_results, graphs, component.component_id)

    def _reconcile_components(
        self,
        parent_id: str,
        drafts: list[IncrementalComponentDraft],
        old_components: list[Component],
        immutable_components: list[Component],
        scope: ScopeGraph,
    ) -> tuple[list[Component], set[str]]:
        old_methods = {component.component_id: self._component_method_names(component) for component in old_components}
        draft_methods = {
            index: set().union(*(scope.cluster_members[cluster_id] for cluster_id in draft.source_cluster_ids))
            for index, draft in enumerate(drafts)
        }
        matching_graph = nx.Graph()
        old_nodes = [f"old:{component.component_id}" for component in old_components]
        draft_nodes = [f"draft:{index}" for index in range(len(drafts))]
        matching_graph.add_nodes_from(old_nodes, bipartite=0)
        matching_graph.add_nodes_from(draft_nodes, bipartite=1)
        for old_rank, component in enumerate(sorted(old_components, key=lambda item: item.component_id)):
            old_clusters = set(component.source_cluster_ids)
            for index, draft in enumerate(drafts):
                method_overlap = len(old_methods[component.component_id] & draft_methods[index])
                cluster_overlap = len(old_clusters & set(draft.source_cluster_ids))
                if method_overlap or cluster_overlap:
                    tie_break = max(0, 999 - old_rank * 50 - index)
                    weight = method_overlap * 1_000_000 + cluster_overlap * 1_000 + tie_break
                    matching_graph.add_edge(f"old:{component.component_id}", f"draft:{index}", weight=weight)
        matching = nx.algorithms.matching.max_weight_matching(matching_graph, maxcardinality=False, weight="weight")
        draft_to_old: dict[int, str] = {}
        for left, right in matching:
            old_node, draft_node = (left, right) if left.startswith("old:") else (right, left)
            draft_to_old[int(draft_node.removeprefix("draft:"))] = old_node.removeprefix("old:")

        sibling_ids = {component.component_id for component in [*old_components, *immutable_components]}
        replacement: list[Component] = []
        new_ids: set[str] = set()
        for index, draft in enumerate(drafts):
            component_id = draft_to_old.get(index)
            if component_id is None:
                component_id = self.id_state.allocate(parent_id, sibling_ids | new_ids)
                new_ids.add(component_id)
            component = Component(
                name=draft.name,
                description=draft.description,
                key_entities=list(draft.key_entities),
                source_cluster_ids=CodeBoardingClusterIds.sort(set(draft.source_cluster_ids)),
                file_methods=self._file_methods_for_clusters(draft.source_cluster_ids, scope),
                component_id=component_id,
            )
            replacement.append(component)
        return replacement, new_ids

    def _file_methods_for_clusters(self, cluster_ids: list[str], scope: ScopeGraph) -> list[FileMethodGroup]:
        qualified_names = set().union(*(scope.cluster_members[cluster_id] for cluster_id in cluster_ids))
        method_names = qualified_names & set(self.current_methods)
        missing = method_names - set(scope.method_to_cluster)
        if missing:
            raise IncrementalAnalysisError(f"Methods are not clustered in the current scope: {sorted(missing)}")
        by_file: dict[str, list[MethodEntry]] = defaultdict(list)
        for qualified_name in sorted(method_names):
            record = self.current_methods[qualified_name]
            by_file[record.file_path].append(record.to_entry())
        return [
            FileMethodGroup(
                file_path=file_path,
                methods=sorted(methods, key=lambda method: (method.start_line, method.end_line, method.qualified_name)),
            )
            for file_path, methods in sorted(by_file.items())
        ]

    def _expand_new_branch(self, component: Component) -> None:
        if self._component_depth(component.component_id) >= self.generator.depth_level:
            return
        component_id, sub_analysis, new_components = self.generator.process_component(component)
        if component_id is None or sub_analysis is None:
            raise IncrementalAnalysisError(f"DetailsAgent failed to expand new component {component.component_id}")
        self.sub_analyses[component_id] = sub_analysis
        for child in new_components:
            self._expand_new_branch(child)

    def _remove_subtree(self, component_id: str) -> None:
        for key in list(self.sub_analyses):
            if key == component_id or key.startswith(f"{component_id}."):
                del self.sub_analyses[key]

    def _refresh_metadata(self) -> None:
        analyses = [self.root_analysis, *self.sub_analyses.values()]
        for analysis in analyses:
            for component in analysis.components:
                current_names = self._component_method_names(component) & set(self.current_methods)
                by_file: dict[str, list[MethodEntry]] = defaultdict(list)
                for qualified_name in sorted(current_names):
                    record = self.current_methods[qualified_name]
                    by_file[record.file_path].append(record.to_entry())
                component.file_methods = [
                    FileMethodGroup(
                        file_path=file_path,
                        methods=sorted(
                            methods,
                            key=lambda method: (method.start_line, method.end_line, method.qualified_name),
                        ),
                    )
                    for file_path, methods in sorted(by_file.items())
                ]
            self.contracts.refresh(analysis)
            self.agent.reference_resolver.fix_source_code_reference_lines(analysis)

    @staticmethod
    def _component_method_names(component: Component) -> set[str]:
        return {method.qualified_name for file_group in component.file_methods for method in file_group.methods}

    @staticmethod
    def _component_depth(component_id: str) -> int:
        return component_id.count(".") + 1

    @staticmethod
    def _sort_components(components: list[Component]) -> list[Component]:
        def key(component: Component) -> tuple[tuple[int, int | str], ...]:
            return tuple((0, int(part)) if part.isdigit() else (1, part) for part in component.component_id.split("."))

        return sorted(components, key=key)

    @staticmethod
    def _component_context(components: list[Component]) -> str:
        return "\n".join(
            f"[{component.component_id}] {component.name}: {component.description}; "
            f"clusters={component.source_cluster_ids}; "
            f"key_entities={[reference.qualified_name for reference in component.key_entities]}"
            for component in components
        )

    @staticmethod
    def _parent_context(parent: Component | None, analysis: AnalysisInsights) -> str:
        if parent is None:
            return f"Project root: {analysis.description}"
        return f"[{parent.component_id}] {parent.name}: {parent.description}"

    def _cluster_context(
        self,
        scope: ScopeGraph,
        cluster_ids: set[str],
        affected_methods: set[str],
    ) -> str:
        lines: list[str] = []
        for cluster_id in CodeBoardingClusterIds.sort(cluster_ids):
            members = sorted(scope.cluster_members[cluster_id])
            important = [member for member in members if member in affected_methods]
            remaining = [member for member in members if member not in affected_methods]
            visible = [*important, *remaining[: max(0, 40 - len(important))]]
            suffix = f" (+{len(members) - len(visible)} more)" if len(visible) < len(members) else ""
            lines.append(f"{cluster_id}: {visible}{suffix}")
        return "\n".join(lines)

    def _method_change_context(self, affected_methods: set[str]) -> str:
        categories = {
            "added": self.delta.added,
            "deleted": self.delta.deleted,
            "modified": self.delta.modified,
            "cluster-reassigned": self.delta.cluster_reassigned,
            "call-boundary": self.delta.call_boundary_changed,
        }
        return "\n".join(
            f"{label}: {sorted(methods & affected_methods)}"
            for label, methods in categories.items()
            if methods & affected_methods
        )

    def _call_change_context(self, affected_methods: set[str]) -> str:
        added = sorted(edge for edge in self.delta.added_calls if set(edge) & affected_methods)
        deleted = sorted(edge for edge in self.delta.deleted_calls if set(edge) & affected_methods)
        parts = []
        if added:
            parts.append(f"added: {added}")
        if deleted:
            parts.append(f"deleted: {deleted}")
        return "\n".join(parts)
