"""Entry point for clustering a call graph."""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Callable, Collection, Mapping
from types import MappingProxyType

import networkx as nx

from clustering_ids import ClusterId, ComponentId, GroupId, ScopeId
from constants import MIN_CLUSTERS_THRESHOLD
from static_analyzer.cfg import CallGraph, DEFAULT_REFERENCE_KINDS
from static_analyzer.config import CALLABLE_TYPES, CLASS_TYPES
from static_analyzer.clustering.engine import cluster_graph
from static_analyzer.clustering.expansion import scope_is_separable, scope_load
from static_analyzer.clustering.grouping import (
    GroupingService,
    combine_cluster_results,
    reindex_across_languages,
    score_grouping,
)
from static_analyzer.clustering.models import (
    METHOD_LEVEL_STRATEGY,
    ClusterConnectionEdge,
    ClusterGroup,
    ClusterResult,
    ClusterScopeInput,
    ClusterScopeResult,
    GroupConnection,
)
from static_analyzer.clustering.repair import repair_member_ownership

_ROOT_SCOPE_ID: ScopeId = "root"
_EMPTY_LEAF_CLUSTERS: Mapping[str, ClusterResult] = MappingProxyType({})
_EMPTY_OWNERS: Mapping[ClusterId, ComponentId] = MappingProxyType({})
_EMPTY_MEMBER_OWNERS: Mapping[str, Mapping[str, ComponentId]] = MappingProxyType({})
_EMPTY_RETAINED_CLUSTER_MEMBERS: Mapping[ClusterId, Collection[str]] = MappingProxyType({})


class LeafClustersUnavailableError(RuntimeError):
    """Raised when a language has symbols but no leaf clusters to own them."""

    def __init__(self, language: str):
        super().__init__(
            f"Language {language!r} has callable or class symbols but no leaf clusters; "
            "enable method-level fallback or provide a usable cluster result."
        )
        self.language = language


def _unseeded_scope(_scope_id: ScopeId, _graphs: Mapping[str, CallGraph]) -> ClusterScopeInput:
    return ClusterScopeInput()


class ClusteringService:
    """Clusters a ``CallGraph``.

    Pure: it neither mutates the graph nor caches anything. Callers that want to
    keep a result store it in the ``ClusterCache`` on their ``LanguageResults``.
    """

    def cluster(self, graph: CallGraph) -> ClusterResult:
        return cluster_graph(graph.to_networkx(DEFAULT_REFERENCE_KINDS), delimiter=graph.delimiter)

    @staticmethod
    def expand_to_method_level(
        graph: CallGraph,
        partition: ClusterResult,
        *,
        next_new_id: ClusterId = 0,
        retained_members_by_cluster: Mapping[ClusterId, Collection[str]] = _EMPTY_RETAINED_CLUSTER_MEMBERS,
    ) -> ClusterResult:
        """Replace a coarse partition with stable one-symbol clusters when needed."""
        if len(partition.clusters) >= MIN_CLUSTERS_THRESHOLD:
            return partition

        clusters: dict[int, set[str]] = {}
        cluster_to_files: dict[int, set[str]] = {}
        file_to_clusters: dict[str, set[int]] = defaultdict(set)
        expanded_members = sorted(
            qualified_name for qualified_name, node in graph.nodes.items() if node.type in CALLABLE_TYPES
        )
        if len(expanded_members) < MIN_CLUSTERS_THRESHOLD:
            expanded_members.extend(
                sorted(qualified_name for qualified_name, node in graph.nodes.items() if node.type in CLASS_TYPES)
            )
        expanded_member_set = set(expanded_members)
        source_cluster_by_member = {
            qualified_name: cluster_id
            for cluster_id, members in partition.clusters.items()
            for qualified_name in members
        }
        retained_member_by_cluster = {
            cluster_id: min(retained)
            for cluster_id, members in partition.clusters.items()
            if (retained := set(members) & set(retained_members_by_cluster.get(cluster_id, ())) & expanded_member_set)
        }
        for cluster_id, members in partition.clusters.items():
            if cluster_id in retained_member_by_cluster:
                continue
            live_members = set(members) & expanded_member_set
            if live_members:
                retained_member_by_cluster[cluster_id] = min(live_members)
        next_new_id = max(next_new_id, max(partition.clusters, default=-1) + 1)

        def add_symbol(qualified_name: str) -> None:
            nonlocal next_new_id
            node = graph.nodes[qualified_name]
            source_cluster_id = source_cluster_by_member.get(qualified_name)
            if source_cluster_id is not None and retained_member_by_cluster.get(source_cluster_id) == qualified_name:
                cluster_id = source_cluster_id
            else:
                cluster_id = next_new_id
                next_new_id += 1
            clusters[cluster_id] = {qualified_name}
            cluster_to_files[cluster_id] = {node.file_path}
            file_to_clusters[node.file_path].add(cluster_id)

        for qualified_name in expanded_members:
            add_symbol(qualified_name)

        return ClusterResult(
            clusters=clusters,
            cluster_to_files=cluster_to_files,
            file_to_clusters=dict(file_to_clusters),
            strategy=METHOD_LEVEL_STRATEGY,
        )

    def cluster_scope(
        self,
        graphs: Mapping[str, CallGraph],
        *,
        scope_id: ScopeId = _ROOT_SCOPE_ID,
        leaf_clusters_by_language: Mapping[str, ClusterResult] = _EMPTY_LEAF_CLUSTERS,
        previous_owner: Mapping[ClusterId, ComponentId] = _EMPTY_OWNERS,
        previous_member_owner: Mapping[str, Mapping[str, ComponentId]] = _EMPTY_MEMBER_OWNERS,
        reserved_group_ids: Collection[GroupId] = (),
        method_level_fallback: bool = False,
    ) -> ClusterScopeResult:
        """Cluster and group one exact graph scope, retaining sibling communication."""
        scope_leaf_clusters: dict[str, ClusterResult] = {}
        for language, graph in graphs.items():
            scope_leaf_clusters[language] = (
                leaf_clusters_by_language[language] if language in leaf_clusters_by_language else self.cluster(graph)
            )
        previous_owner_by_language_member = {
            language: {
                qualified_name: previous_owner[cluster_id]
                for cluster_id, members in cluster_result.clusters.items()
                if previous_owner.get(cluster_id)
                for qualified_name in members
            }
            for language, cluster_result in scope_leaf_clusters.items()
        }
        if method_level_fallback:
            scope_leaf_clusters = {
                language: self.expand_to_method_level(graphs[language], cluster_result)
                for language, cluster_result in scope_leaf_clusters.items()
            }
        for language, graph in graphs.items():
            if not scope_leaf_clusters[language].clusters and any(
                node.type in CALLABLE_TYPES | CLASS_TYPES for node in graph.nodes.values()
            ):
                raise LeafClustersUnavailableError(language)
        reindex_across_languages(scope_leaf_clusters)
        scope_previous_owner: dict[ClusterId, ComponentId] = {}
        for language, cluster_result in scope_leaf_clusters.items():
            owner_by_member = previous_owner_by_language_member[language]
            for cluster_id, members in cluster_result.clusters.items():
                owner_counts = Counter(owner_by_member[member] for member in members if member in owner_by_member)
                if owner_counts:
                    scope_previous_owner[cluster_id] = min(
                        owner_counts.items(),
                        key=lambda claim: (-claim[1], claim[0]),
                    )[0]

        nx_graphs = {language: graph.to_networkx(DEFAULT_REFERENCE_KINDS) for language, graph in graphs.items()}
        grouping_service = GroupingService()
        subcomponents = scope_id != _ROOT_SCOPE_ID
        if previous_owner:
            grouping = grouping_service.anchored_group(
                scope_leaf_clusters,
                nx_graphs,
                scope_previous_owner,
                subcomponents=subcomponents,
            )
            raw_groups = grouping.groups
            owners = grouping.owners
            combined = combine_cluster_results(scope_leaf_clusters)
            combined_cfg = nx.compose_all(list(nx_graphs.values())) if nx_graphs else nx.DiGraph()
            modularity = score_grouping(combined, combined_cfg, raw_groups)
            unanchored_modularity = grouping.unanchored_modularity
            regrouped = grouping.regrouped
        else:
            raw_groups, modularity = grouping_service.group(
                scope_leaf_clusters,
                nx_graphs,
                subcomponents=subcomponents,
            )
            owners = [""] * len(raw_groups)
            unanchored_modularity = modularity
            regrouped = False

        group_ids = self._allocate_group_ids(scope_id, owners, reserved_group_ids)
        groups = [
            ClusterGroup(
                group_id=group_id,
                cluster_ids=sorted(cluster_ids),
                previous_component_id=owner,
            )
            for group_id, cluster_ids, owner in zip(group_ids, raw_groups, owners, strict=True)
        ]
        self._assign_symbol_members(graphs, scope_leaf_clusters, groups)
        repair_member_ownership(groups, previous_member_owner)
        connections = self._build_connections(graphs, groups)
        return ClusterScopeResult(
            scope_id=scope_id,
            graphs_by_language=dict(graphs),
            leaf_clusters_by_language=scope_leaf_clusters,
            groups=groups,
            connections=connections,
            modularity=modularity,
            unanchored_modularity=unanchored_modularity,
            regrouped=regrouped,
        )

    def cluster_hierarchy(
        self,
        graphs: Mapping[str, CallGraph],
        max_depth: int,
        scope_input: Callable[[ScopeId, Mapping[str, CallGraph]], ClusterScopeInput] = _unseeded_scope,
        root_scope_id: ScopeId = _ROOT_SCOPE_ID,
    ) -> ClusterScopeResult:
        """Recursively cluster every expandable exact subgraph up to ``max_depth``."""
        if max_depth < 1:
            raise ValueError("max_depth must be at least 1")
        root_input = scope_input(root_scope_id, graphs)
        root = self.cluster_scope(
            graphs,
            scope_id=root_scope_id,
            leaf_clusters_by_language=root_input.leaf_clusters_by_language,
            previous_owner=root_input.previous_owner,
            previous_member_owner=root_input.previous_member_owner,
            reserved_group_ids=root_input.reserved_group_ids,
            method_level_fallback=root_scope_id != _ROOT_SCOPE_ID,
        )
        self._cluster_children(root, graphs, 1, max_depth, scope_input)
        root.index_hierarchy()
        return root

    @staticmethod
    def _induced_graphs(group: ClusterGroup, graphs: Mapping[str, CallGraph]) -> dict[str, CallGraph]:
        """Return the exact per-language subgraphs owned by ``group``."""
        child_graphs: dict[str, CallGraph] = {}
        for language, graph in graphs.items():
            members = group.symbol_members_by_language.get(language, set())
            if not members:
                continue
            child = graph.filter_by_nodes(members)
            if child.nodes:
                child_graphs[language] = child
        return child_graphs

    def _cluster_children(
        self,
        scope: ClusterScopeResult,
        graphs: Mapping[str, CallGraph],
        depth: int,
        max_depth: int,
        scope_input: Callable[[ScopeId, Mapping[str, CallGraph]], ClusterScopeInput],
    ) -> None:
        for group in scope.groups:
            child_graphs = self._induced_graphs(group, graphs)
            method_count, file_count = self._scope_size(child_graphs)
            if not child_graphs or not file_count:
                continue
            child_input = scope_input(group.group_id, child_graphs)
            child = self.cluster_scope(
                child_graphs,
                scope_id=group.group_id,
                leaf_clusters_by_language=child_input.leaf_clusters_by_language,
                previous_owner=child_input.previous_owner,
                previous_member_owner=child_input.previous_member_owner,
                reserved_group_ids=child_input.reserved_group_ids,
                method_level_fallback=True,
            )
            if (
                not child_input.retain_scope
                and sum(bool(child_group.qualified_names) for child_group in child.groups) < 2
            ):
                continue
            load = scope_load(method_count, file_count)
            if (
                not child_input.retain_scope
                and load < 1.0
                and not scope_is_separable(
                    child.leaf_clusters_by_language,
                    child.unanchored_modularity,
                    load,
                    method_count,
                )
            ):
                continue
            group.expandable = True
            if depth >= max_depth:
                continue
            group.children = child
            self._cluster_children(child, child_graphs, depth + 1, max_depth, scope_input)

    @staticmethod
    def _scope_size(graphs: Mapping[str, CallGraph]) -> tuple[int, int]:
        files: set[str] = set()
        method_count = 0
        for graph in graphs.values():
            for node in graph.nodes.values():
                files.add(node.file_path)
                if node.type in CALLABLE_TYPES:
                    method_count += 1
        return method_count, len(files)

    @staticmethod
    def _allocate_group_ids(
        scope_id: ScopeId,
        owners: list[ComponentId],
        reserved_group_ids: Collection[GroupId],
    ) -> list[GroupId]:
        allocated = (set(owners) | set(reserved_group_ids)) - {""}
        result: list[GroupId] = []
        prefix = "" if scope_id == _ROOT_SCOPE_ID else f"{scope_id}."
        used_indices = [
            int(group_id.removeprefix(prefix))
            for group_id in allocated
            if group_id.startswith(prefix) and group_id.removeprefix(prefix).isdigit()
        ]
        next_index = max(used_indices, default=0) + 1
        for owner in owners:
            if owner:
                result.append(owner)
                continue
            while True:
                candidate = str(next_index) if scope_id == _ROOT_SCOPE_ID else f"{scope_id}.{next_index}"
                next_index += 1
                if candidate not in allocated:
                    allocated.add(candidate)
                    result.append(candidate)
                    break
        return result

    def _assign_symbol_members(
        self,
        graphs: Mapping[str, CallGraph],
        leaf_clusters_by_language: dict[str, ClusterResult],
        groups: list[ClusterGroup],
    ) -> None:
        if not groups:
            return
        group_by_cluster = {cluster_id: group for group in groups for cluster_id in group.cluster_ids}
        for language, graph in graphs.items():
            cluster_result = leaf_clusters_by_language.get(language, ClusterResult())
            cluster_by_qualified_name = {
                qualified_name: cluster_id
                for cluster_id, members in cluster_result.clusters.items()
                for qualified_name in members
            }
            undirected = graph.to_networkx(reference_kinds=()).to_undirected()
            for qualified_name, node in graph.nodes.items():
                if node.type not in CALLABLE_TYPES | CLASS_TYPES:
                    continue
                cluster_id = cluster_by_qualified_name.get(qualified_name)
                if cluster_id not in group_by_cluster:
                    cluster_id = self._cluster_for_file(node.file_path, cluster_result)
                if cluster_id not in group_by_cluster:
                    cluster_id = self._nearest_cluster(qualified_name, cluster_result, undirected)
                group = group_by_cluster[cluster_id] if cluster_id is not None else groups[0]
                group.symbol_members_by_language.setdefault(language, set()).add(qualified_name)

    @staticmethod
    def _cluster_for_file(file_path: str, cluster_result: ClusterResult) -> ClusterId | None:
        return next(iter(cluster_result.get_clusters_for_file(file_path)), None)

    @staticmethod
    def _nearest_cluster(qualified_name: str, cluster_result: ClusterResult, graph: nx.Graph) -> ClusterId | None:
        if qualified_name not in graph:
            return None
        distances = nx.single_source_shortest_path_length(graph, qualified_name)
        nearest: ClusterId | None = None
        nearest_distance = float("inf")
        for cluster_id, members in cluster_result.clusters.items():
            for member in members:
                distance = distances.get(member)
                if distance is not None and distance < nearest_distance:
                    nearest = cluster_id
                    nearest_distance = distance
        return nearest

    @staticmethod
    def _build_connections(graphs: Mapping[str, CallGraph], groups: list[ClusterGroup]) -> list[GroupConnection]:
        group_id_by_qualified_name = {
            (language, qualified_name): group.group_id
            for group in groups
            for language, qualified_names in group.symbol_members_by_language.items()
            for qualified_name in qualified_names
        }
        by_pair: dict[tuple[GroupId, GroupId], GroupConnection] = {}
        for language, graph in graphs.items():
            for edge in graph.edges:
                source = edge.get_source()
                target = edge.get_destination()
                source_group = group_id_by_qualified_name.get((language, source), "")
                target_group = group_id_by_qualified_name.get((language, target), "")
                if not source_group or not target_group or source_group == target_group:
                    continue
                pair = (source_group, target_group)
                connection = by_pair.setdefault(
                    pair,
                    GroupConnection(source_group_id=source_group, target_group_id=target_group),
                )
                connection.edges.append(
                    ClusterConnectionEdge(
                        language=language,
                        source_qualified_name=source,
                        target_qualified_name=target,
                        call_sites=edge.call_sites,
                    )
                )
        return [by_pair[pair] for pair in sorted(by_pair)]
