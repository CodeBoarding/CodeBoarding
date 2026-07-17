"""Breadth-first scope processing for incremental architecture patches."""

from collections import defaultdict
import logging

import networkx as nx

from agents.analysis_result_responses import AnalysisInsights, Component, SourceCodeReference
from agents.cluster_ids import CodeBoardingClusterIds
from agents.content_hash import SourceCache
from agents.file_index_models import FileMethodGroup, MethodEntry
from agents.incremental_agent import IncrementalAgent
from diagram_analysis.diagram_generator import DiagramGenerator
from diagram_analysis.file_index import build_files_index
from diagram_analysis.incremental.contracts import IncrementalContractsUpdater
from diagram_analysis.incremental.errors import IncrementalAnalysisError
from diagram_analysis.incremental.impact import IncrementalImpactAnalyzer, component_method_names
from diagram_analysis.incremental.models import (
    ComponentContent,
    ComponentContentContext,
    ScopeDescription,
    ScopeGraph,
    ScopePartition,
    ScopePatchContext,
    ScopeTask,
    ScopeUpdate,
)
from diagram_analysis.incremental.state import IncrementalIdState
from static_analyzer.analysis_result import StaticAnalysisResults
from static_analyzer.cluster_helpers import build_all_cluster_results, reindex_cross_language_clusters
from static_analyzer.clustering import ClusterResult
from static_analyzer.program_graph import ProgramGraph

logger = logging.getLogger(__name__)

MAX_CLUSTER_CONTEXT_METHODS = 20
MAX_CHANGE_CONTEXT_ITEMS = 50


class IncrementalScopeProcessor:
    """Patch direct children one scope and one depth at a time."""

    def __init__(
        self,
        generator: DiagramGenerator,
        agent: IncrementalAgent,
        previous_static: StaticAnalysisResults,
        root_analysis: AnalysisInsights,
        sub_analyses: dict[str, AnalysisInsights],
        id_state: IncrementalIdState,
    ) -> None:
        if generator.static_analysis is None or generator.details_agent is None:
            raise IncrementalAnalysisError("Incremental generator has not completed pre-analysis")
        self.generator = generator
        self.agent = agent
        self.previous_static = previous_static
        self.current_static = generator.static_analysis
        self.root_analysis = root_analysis
        self.sub_analyses = sub_analyses
        self.id_state = id_state
        self.contracts = IncrementalContractsUpdater(agent)
        self.changed_component_ids: set[str] = set()
        self.removed_component_ids: set[str] = set()
        self.previous_root_scope = previous_root_scope(previous_static)
        self.current_root_scope = current_root_scope(self.current_static)
        self.impact = IncrementalImpactAnalyzer(
            generator.repo_location,
            previous_static,
            self.current_static,
            root_analysis,
            self.previous_root_scope,
            self.current_root_scope,
        )

    def run(self) -> tuple[AnalysisInsights, dict[str, AnalysisInsights]]:
        tasks = [ScopeTask(component_id="", depth=0)]
        while tasks:
            depth = tasks[0].depth
            current_level = [task for task in tasks if task.depth == depth]
            tasks = [task for task in tasks if task.depth != depth]
            logger.info("Processing %d incremental scope(s) at depth %d", len(current_level), depth)
            for task in current_level:
                tasks.extend(self._process_task(task))
        self._refresh_metadata()
        return self.root_analysis, self.sub_analyses

    def _process_task(self, task: ScopeTask) -> list[ScopeTask]:
        if task.depth >= self.generator.depth_level:
            return []
        if task.component_id and (task.is_new or task.component_id not in self.sub_analyses):
            return self._expand_new_scope(task)
        analysis = self.root_analysis if not task.component_id else self.sub_analyses[task.component_id]
        parent = self._component(task.component_id) if task.component_id else None
        scope = self.current_root_scope if parent is None else self._current_child_scope(parent)
        if parent is not None and len(scope.cluster_members) <= 1:
            self.removed_component_ids.update(
                child.component_id
                for scope_id, sub_analysis in self.sub_analyses.items()
                if scope_id == parent.component_id or scope_id.startswith(f"{parent.component_id}.")
                for child in sub_analysis.components
            )
            self._remove_subtree(parent.component_id)
            return []
        if not scope.cluster_members:
            return []
        update = self._patch_scope(analysis, parent, scope)
        return [
            ScopeTask(component_id=component_id, depth=task.depth + 1, is_new=component_id in update.new_component_ids)
            for component_id in sorted(update.affected_component_ids, key=component_id_key)
        ]

    def _patch_scope(
        self,
        analysis: AnalysisInsights,
        parent: Component | None,
        scope: ScopeGraph,
    ) -> ScopeUpdate:
        impact = self.impact.scope_impact(analysis.components, scope)
        component_by_id = {component.component_id: component for component in analysis.components}
        for component_id, cluster_ids in impact.immutable_cluster_assignments.items():
            component_by_id[component_id].source_cluster_ids = cluster_ids
        logger.info(
            "Scoped ownership preflight for %s: modules=%d mutable=%d frozen=%d deleted=%d",
            parent.component_id if parent is not None else "root",
            len(scope.cluster_members),
            len(impact.mutable_cluster_ids),
            len(impact.immutable_cluster_assignments),
            len(impact.deleted_component_ids),
        )
        if not impact.mutable_cluster_ids and not impact.deleted_component_ids:
            return ScopeUpdate()

        parent_id = parent.component_id if parent is not None else ""
        for component_id in impact.deleted_component_ids:
            self._remove_subtree(component_id)

        live_components = [
            component for component in analysis.components if component.component_id not in impact.deleted_component_ids
        ]
        analysis.components = live_components
        mutable_components = [
            component for component in live_components if component.component_id in impact.mutable_component_ids
        ]
        immutable_components = [
            component for component in live_components if component.component_id not in impact.mutable_component_ids
        ]
        architecture_outline = self._architecture_outline(parent, live_components)
        parent_context = self._parent_context(parent, analysis)
        partition = self.agent.patch_partition(
            ScopePatchContext(
                architecture_outline=architecture_outline,
                parent=parent_context,
                immutable_components=self._component_context(immutable_components, compact=True),
                mutable_components=self._component_context(mutable_components, compact=True),
                current_modules=self._cluster_context(
                    scope,
                    impact.mutable_cluster_ids,
                    impact.affected_methods,
                ),
                method_changes=self._method_change_context(impact.affected_methods),
                call_changes=self._call_change_context(impact.affected_methods),
                expected_cluster_ids=impact.mutable_cluster_ids,
            ),
            impact.proposed_partition,
        )
        self._reconcile_partition_ids(partition, mutable_components, scope)

        replacements, new_ids = self._materialize_partition(
            parent_id,
            partition,
            component_by_id,
            impact.mutable_component_ids,
            immutable_components,
            scope,
            impact.affected_methods,
            architecture_outline,
            parent_context,
        )
        replacement_ids = {component.component_id for component in replacements}
        removed_ids = impact.deleted_component_ids | (impact.mutable_component_ids - replacement_ids)
        for component_id in removed_ids:
            self._remove_subtree(component_id)

        analysis.components = sorted(replacements, key=lambda component: component_id_key(component.component_id))
        changed_ids = replacement_ids - {
            component.component_id
            for component in immutable_components
            if component.component_id in replacement_ids and component_by_id[component.component_id] is component
        }
        changed_ids.update(new_ids)
        self.changed_component_ids.update(changed_ids)
        self.removed_component_ids.update(removed_ids)
        analysis.description = self.agent.patch_scope_description(
            self._architecture_outline(parent, analysis.components),
            parent_context,
            self._component_context(analysis.components),
            ScopeDescription(description=analysis.description),
        ).description
        self.contracts.update(analysis, scope.graphs, changed_ids, removed_ids)
        return ScopeUpdate(affected_component_ids=changed_ids, new_component_ids=new_ids)

    @staticmethod
    def _reconcile_partition_ids(
        partition: ScopePartition,
        old_components: list[Component],
        scope: ScopeGraph,
    ) -> None:
        """Assign surviving IDs by maximum method overlap after LLM grouping."""
        groups = list(partition.groups.items())
        matching_graph = nx.Graph()
        old_nodes = [f"old:{component.component_id}" for component in old_components]
        group_nodes = [f"group:{key}" for key, _group in groups]
        matching_graph.add_nodes_from(old_nodes, bipartite=0)
        matching_graph.add_nodes_from(group_nodes, bipartite=1)

        old_methods = {component.component_id: component_method_names(component) for component in old_components}
        group_methods = {
            group_key: set().union(*(scope.cluster_members[cluster_id] for cluster_id in group.cluster_ids))
            for group_key, group in groups
        }
        for old_rank, component in enumerate(
            sorted(old_components, key=lambda item: component_id_key(item.component_id))
        ):
            old_clusters = set(component.source_cluster_ids)
            for group_rank, (group_key, group) in enumerate(groups):
                method_overlap = len(old_methods[component.component_id] & group_methods[group_key])
                cluster_overlap = len(old_clusters & set(group.cluster_ids))
                if not method_overlap and not cluster_overlap:
                    continue
                tie_break = max(0, 999 - old_rank * 50 - group_rank)
                weight = method_overlap * 1_000_000 + cluster_overlap * 1_000 + tie_break
                matching_graph.add_edge(
                    f"old:{component.component_id}",
                    f"group:{group_key}",
                    weight=weight,
                )

        matching = nx.algorithms.matching.max_weight_matching(
            matching_graph,
            maxcardinality=False,
            weight="weight",
        )
        for _key, group in groups:
            group.component_id = ""
        for left, right in matching:
            old_node, group_node = (left, right) if left.startswith("old:") else (right, left)
            partition.groups[group_node.removeprefix("group:")].component_id = old_node.removeprefix("old:")

    def _materialize_partition(
        self,
        parent_id: str,
        partition: ScopePartition,
        component_by_id: dict[str, Component],
        mutable_ids: set[str],
        immutable_components: list[Component],
        scope: ScopeGraph,
        affected_methods: set[str],
        architecture_outline: str,
        parent_context: str,
    ) -> tuple[list[Component], set[str]]:
        groups = list(partition.groups.values())
        reused_ids = {group.component_id for group in groups if group.component_id}
        replacements = [component for component in immutable_components if component.component_id not in reused_ids]
        sibling_ids = set(component_by_id)
        new_ids: set[str] = set()
        for group in groups:
            existing = component_by_id.get(group.component_id) if group.component_id else None
            if existing is not None and existing.component_id not in mutable_ids:
                raise IncrementalAnalysisError(
                    f"Incremental partition attempted to modify immutable component {existing.component_id}"
                )
            component_id = group.component_id
            if not component_id:
                component_id = self.id_state.allocate(parent_id, sibling_ids | new_ids)
                new_ids.add(component_id)
            cluster_ids = set(group.cluster_ids)
            missing_clusters = cluster_ids - set(scope.cluster_members)
            if missing_clusters:
                raise IncrementalAnalysisError(
                    f"Current scope does not contain clusters retained by {component_id}: {sorted(missing_clusters)}"
                )
            sorted_cluster_ids = CodeBoardingClusterIds.sort(cluster_ids)
            allowed_names = set().union(*(scope.cluster_members[cluster_id] for cluster_id in sorted_cluster_ids))
            initial = ComponentContent(
                name=existing.name if existing is not None else "New Component",
                description=(
                    existing.description
                    if existing is not None
                    else "New responsibility represented by the assigned current clusters."
                ),
                key_entity_qualified_names=(
                    [reference.qualified_name for reference in existing.key_entities] if existing is not None else []
                ),
            )
            content = self.agent.patch_component_content(
                ComponentContentContext(
                    architecture_outline=architecture_outline,
                    parent=parent_context,
                    component_id=component_id,
                    current_clusters=self._cluster_context(scope, set(sorted_cluster_ids), affected_methods),
                    method_changes=self._method_change_context(allowed_names & affected_methods),
                    call_changes=self._call_change_context(allowed_names & affected_methods),
                    allowed_qualified_names=allowed_names,
                    is_new=existing is None,
                ),
                initial,
            )
            replacements.append(
                Component(
                    name=content.name,
                    description=content.description,
                    key_entities=self.agent.reference_resolver.repair_key_entity_references(
                        [
                            SourceCodeReference(qualified_name=qualified_name)
                            for qualified_name in content.key_entity_qualified_names
                        ],
                        allowed_qnames=allowed_names,
                    ).references,
                    source_cluster_ids=sorted_cluster_ids,
                    file_methods=self._file_methods_for_clusters(sorted_cluster_ids, scope),
                    component_id=component_id,
                )
            )
        return replacements, new_ids

    def _expand_new_scope(self, task: ScopeTask) -> list[ScopeTask]:
        component = self._component(task.component_id)
        child_scope = self._current_child_scope(component)
        if sum(len(result.clusters) for result in child_scope.cluster_results.values()) <= 1:
            return []
        component_id, sub_analysis, children = self.generator.process_component(component)
        if component_id is None or sub_analysis is None:
            raise IncrementalAnalysisError(f"DetailsAgent failed to expand new component {task.component_id}")
        self.sub_analyses[component_id] = sub_analysis
        return [
            ScopeTask(component_id=child.component_id, depth=task.depth + 1, is_new=True)
            for child in sorted(children, key=lambda item: component_id_key(item.component_id))
        ]

    def _current_child_scope(self, component: Component) -> ScopeGraph:
        details_agent = self.generator.details_agent
        if details_agent is None:
            raise IncrementalAnalysisError("DetailsAgent is unavailable")
        _, cluster_results, graphs = details_agent._create_strict_component_subgraph(
            component,
            source_cluster_id_prefix=component.component_id,
        )
        return scope_graph(cluster_results, graphs, component.component_id)

    def _file_methods_for_clusters(self, cluster_ids: list[str], scope: ScopeGraph) -> list[FileMethodGroup]:
        qualified_names = set().union(*(scope.cluster_members[cluster_id] for cluster_id in cluster_ids))
        by_file: dict[str, list[MethodEntry]] = defaultdict(list)
        for qualified_name in sorted(qualified_names & set(self.impact.current_methods)):
            record = self.impact.current_methods[qualified_name]
            by_file[record.file_path].append(record.to_entry())
        return [
            FileMethodGroup(
                file_path=file_path,
                methods=sorted(methods, key=lambda method: (method.start_line, method.end_line, method.qualified_name)),
            )
            for file_path, methods in sorted(by_file.items())
        ]

    def _refresh_metadata(self) -> None:
        source_cache: SourceCache = {}
        graphs = self.current_static.available_program_graphs()
        for analysis in [self.root_analysis, *self.sub_analyses.values()]:
            for component in analysis.components:
                current_names = component_method_names(component) & set(self.impact.current_methods)
                by_file: dict[str, list[MethodEntry]] = defaultdict(list)
                for qualified_name in sorted(current_names):
                    record = self.impact.current_methods[qualified_name]
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
            analysis.files = build_files_index(analysis, self.generator.repo_location, source_cache)
            self.contracts.refresh_static_edges(analysis, graphs)
            self.agent.reference_resolver.fix_source_code_reference_lines(analysis)

    def _component(self, component_id: str) -> Component:
        for analysis in [self.root_analysis, *self.sub_analyses.values()]:
            for component in analysis.components:
                if component.component_id == component_id:
                    return component
        raise IncrementalAnalysisError(f"Component {component_id!r} is absent from the candidate analysis")

    def _remove_subtree(self, component_id: str) -> None:
        for key in list(self.sub_analyses):
            if key == component_id or key.startswith(f"{component_id}."):
                del self.sub_analyses[key]

    def _architecture_outline(self, parent: Component | None, children: list[Component]) -> str:
        lines = [f"[root] {self.root_analysis.description}"]
        if parent is not None:
            parts = parent.component_id.split(".")
            ancestor_ids = [".".join(parts[:index]) for index in range(1, len(parts) + 1)]
            lines.extend(
                f"[{component.component_id}] {component.name}: {component.description}"
                for component in (self._component(component_id) for component_id in ancestor_ids)
            )
        for component in sorted(children, key=lambda item: component_id_key(item.component_id)):
            lines.append(f"[{component.component_id}] {component.name}: {component.description}")
        return "\n".join(lines)

    @staticmethod
    def _component_context(components: list[Component], compact: bool = False) -> str:
        if compact:
            return "\n".join(
                f"[{component.component_id}] {component.name}: {component.description}" for component in components
            )
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

    @staticmethod
    def _cluster_context(scope: ScopeGraph, cluster_ids: set[str], affected_methods: set[str]) -> str:
        lines: list[str] = []
        for cluster_id in CodeBoardingClusterIds.sort(cluster_ids):
            members = sorted(scope.cluster_members[cluster_id])
            important = [member for member in members if member in affected_methods]
            remaining = [member for member in members if member not in affected_methods]
            visible_important = important[:MAX_CLUSTER_CONTEXT_METHODS]
            visible = [
                *visible_important,
                *remaining[: max(0, MAX_CLUSTER_CONTEXT_METHODS - len(visible_important))],
            ]
            omitted_affected = len(important) - len(visible_important)
            omitted_unchanged = len(members) - len(important) - (len(visible) - len(visible_important))
            omitted = []
            if omitted_affected:
                omitted.append(f"{omitted_affected} affected")
            if omitted_unchanged:
                omitted.append(f"{omitted_unchanged} unchanged")
            suffix = f" (+{', '.join(omitted)} omitted)" if omitted else ""
            lines.append(f"{cluster_id}: {visible}{suffix}")
        return "\n".join(lines)

    def _method_change_context(self, affected_methods: set[str]) -> str:
        categories = {
            "added": self.impact.delta.added,
            "deleted": self.impact.delta.deleted,
            "modified": self.impact.delta.modified,
            "cluster-reassigned": self.impact.delta.cluster_reassigned,
            "call-boundary": self.impact.delta.call_boundary_changed,
        }
        lines = []
        for label, methods in categories.items():
            matching = sorted(methods & affected_methods)
            if not matching:
                continue
            suffix = (
                f" (+{len(matching) - MAX_CHANGE_CONTEXT_ITEMS} omitted)"
                if len(matching) > MAX_CHANGE_CONTEXT_ITEMS
                else ""
            )
            lines.append(f"{label}: {matching[:MAX_CHANGE_CONTEXT_ITEMS]}{suffix}")
        return "\n".join(lines)

    def _call_change_context(self, affected_methods: set[str]) -> str:
        added = sorted(edge for edge in self.impact.delta.added_calls if set(edge) & affected_methods)
        deleted = sorted(edge for edge in self.impact.delta.deleted_calls if set(edge) & affected_methods)
        parts = []
        if added:
            suffix = (
                f" (+{len(added) - MAX_CHANGE_CONTEXT_ITEMS} omitted)" if len(added) > MAX_CHANGE_CONTEXT_ITEMS else ""
            )
            parts.append(f"added: {added[:MAX_CHANGE_CONTEXT_ITEMS]}{suffix}")
        if deleted:
            suffix = (
                f" (+{len(deleted) - MAX_CHANGE_CONTEXT_ITEMS} omitted)"
                if len(deleted) > MAX_CHANGE_CONTEXT_ITEMS
                else ""
            )
            parts.append(f"deleted: {deleted[:MAX_CHANGE_CONTEXT_ITEMS]}{suffix}")
        return "\n".join(parts)


def previous_root_scope(static_analysis: StaticAnalysisResults) -> ScopeGraph:
    cluster_results: dict[str, ClusterResult] = {}
    graphs = static_analysis.available_program_graphs()
    for language, graph in graphs.items():
        if graph.cluster_snapshot is None:
            raise IncrementalAnalysisError(f"Missing Infomap lineage for {language}; run a full analysis first")
        cluster_results[str(language)] = copy_cluster_result(graph.cluster_snapshot.cluster_result)
    if len(cluster_results) > 1:
        reindex_cross_language_clusters(cluster_results)
    return scope_graph(cluster_results, graphs, "")


def current_root_scope(static_analysis: StaticAnalysisResults) -> ScopeGraph:
    return scope_graph(build_all_cluster_results(static_analysis), static_analysis.available_program_graphs(), "")


def scope_graph(
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


def copy_cluster_result(result: ClusterResult) -> ClusterResult:
    return ClusterResult(
        clusters={cluster_id: set(members) for cluster_id, members in result.clusters.items()},
        cluster_to_files={cluster_id: set(paths) for cluster_id, paths in result.cluster_to_files.items()},
        file_to_clusters={path: set(cluster_ids) for path, cluster_ids in result.file_to_clusters.items()},
        strategy=result.strategy,
    )


def component_id_key(component_id: str) -> tuple[tuple[int, int | str], ...]:
    return tuple((0, int(part)) if part.isdigit() else (1, part) for part in component_id.split("."))
