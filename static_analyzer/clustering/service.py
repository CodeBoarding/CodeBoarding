"""Entry point for clustering a call graph."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Mapping
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

_ROOT_SCOPE_ID: ScopeId = "root"
_EMPTY_LEAF_CLUSTERS: Mapping[str, ClusterResult] = MappingProxyType({})
_EMPTY_OWNERS: Mapping[ClusterId, ComponentId] = MappingProxyType({})


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

    def cluster_scope(
        self,
        graphs: Mapping[str, CallGraph],
        *,
        scope_id: ScopeId = _ROOT_SCOPE_ID,
        leaf_clusters_by_language: Mapping[str, ClusterResult] = _EMPTY_LEAF_CLUSTERS,
        previous_owner: Mapping[ClusterId, ComponentId] = _EMPTY_OWNERS,
        method_level_fallback: bool = False,
    ) -> ClusterScopeResult:
        """Cluster and group one exact graph scope, retaining sibling communication."""
        scope_leaf_clusters: dict[str, ClusterResult] = {}
        for language, graph in graphs.items():
            scope_leaf_clusters[language] = (
                leaf_clusters_by_language[language] if language in leaf_clusters_by_language else self.cluster(graph)
            )
        if method_level_fallback:
            scope_leaf_clusters = {
                language: self._expand_to_method_level(graphs[language], cluster_result)
                for language, cluster_result in scope_leaf_clusters.items()
            }
        for language, graph in graphs.items():
            if not scope_leaf_clusters[language].clusters and any(
                node.type in CALLABLE_TYPES | CLASS_TYPES for node in graph.nodes.values()
            ):
                raise LeafClustersUnavailableError(language)
        reindex_across_languages(scope_leaf_clusters)

        nx_graphs = {language: graph.to_networkx(DEFAULT_REFERENCE_KINDS) for language, graph in graphs.items()}
        grouping_service = GroupingService()
        subcomponents = scope_id != _ROOT_SCOPE_ID
        if previous_owner:
            grouping = grouping_service.anchored_group(
                scope_leaf_clusters,
                nx_graphs,
                dict(previous_owner),
                subcomponents=subcomponents,
            )
            raw_groups = grouping.groups
            owners = grouping.owners
            combined = combine_cluster_results(scope_leaf_clusters)
            combined_cfg = nx.compose_all(list(nx_graphs.values())) if nx_graphs else nx.DiGraph()
            modularity = score_grouping(combined, combined_cfg, raw_groups)
            fresh_modularity = grouping.fresh_modularity
            regrouped = grouping.regrouped
        else:
            raw_groups, modularity = grouping_service.group(
                scope_leaf_clusters,
                nx_graphs,
                subcomponents=subcomponents,
            )
            owners = [""] * len(raw_groups)
            fresh_modularity = modularity
            regrouped = False

        group_ids = self._allocate_group_ids(scope_id, owners)
        groups = [
            ClusterGroup(
                group_id=group_id,
                cluster_ids=sorted(cluster_ids),
                previous_component_id=owner,
            )
            for group_id, cluster_ids, owner in zip(group_ids, raw_groups, owners, strict=True)
        ]
        self._assign_symbol_members(graphs, scope_leaf_clusters, groups)
        connections = self._build_connections(graphs, groups)
        return ClusterScopeResult(
            scope_id=scope_id,
            leaf_clusters_by_language=scope_leaf_clusters,
            groups=groups,
            connections=connections,
            modularity=modularity,
            fresh_modularity=fresh_modularity,
            regrouped=regrouped,
        )

    def cluster_hierarchy(
        self,
        graphs: Mapping[str, CallGraph],
        max_depth: int,
        scope_input: Callable[[ScopeId, Mapping[str, CallGraph]], ClusterScopeInput] = _unseeded_scope,
    ) -> ClusterScopeResult:
        """Recursively cluster every expandable exact subgraph up to ``max_depth``."""
        if max_depth < 1:
            raise ValueError("max_depth must be at least 1")
        root_input = scope_input(_ROOT_SCOPE_ID, graphs)
        root = self.cluster_scope(
            graphs,
            leaf_clusters_by_language=root_input.leaf_clusters_by_language,
            previous_owner=root_input.previous_owner,
        )
        self._cluster_children(root, graphs, 1, max_depth, scope_input)
        return root

    def _cluster_children(
        self,
        scope: ClusterScopeResult,
        graphs: Mapping[str, CallGraph],
        depth: int,
        max_depth: int,
        scope_input: Callable[[ScopeId, Mapping[str, CallGraph]], ClusterScopeInput],
    ) -> None:
        if depth >= max_depth:
            return
        for group in scope.groups:
            child_graphs = self._induced_graphs(group, graphs)
            method_count, file_count = self._group_size(group, graphs)
            if not child_graphs or not file_count:
                continue
            child_input = scope_input(group.group_id, child_graphs)
            child = self.cluster_scope(
                child_graphs,
                scope_id=group.group_id,
                leaf_clusters_by_language=child_input.leaf_clusters_by_language,
                previous_owner=child_input.previous_owner,
                method_level_fallback=True,
            )
            load = scope_load(method_count, file_count)
            if load < 1.0 and not scope_is_separable(
                child.leaf_clusters_by_language,
                child.fresh_modularity,
                load,
            ):
                continue
            group.children = child
            self._cluster_children(child, child_graphs, depth + 1, max_depth, scope_input)

    @staticmethod
    def _induced_graphs(group: ClusterGroup, graphs: Mapping[str, CallGraph]) -> dict[str, CallGraph]:
        child_graphs: dict[str, CallGraph] = {}
        for language, graph in graphs.items():
            members = group.symbol_members_by_language.get(language, set())
            if not members:
                continue
            child = graph.filter_by_nodes(members)
            if child.nodes:
                child_graphs[language] = child
        return child_graphs

    @staticmethod
    def _group_size(group: ClusterGroup, graphs: Mapping[str, CallGraph]) -> tuple[int, int]:
        files: set[str] = set()
        method_count = 0
        for language, members in group.symbol_members_by_language.items():
            graph = graphs[language]
            for qualified_name in members:
                node = graph.nodes[qualified_name]
                files.add(node.file_path)
                method_count += 1
        return method_count, len(files)

    @staticmethod
    def _allocate_group_ids(scope_id: ScopeId, owners: list[ComponentId]) -> list[GroupId]:
        allocated = set(owners) - {""}
        used_indices = {
            int(group_id.rsplit(".", maxsplit=1)[-1])
            for group_id in allocated
            if group_id.rsplit(".", maxsplit=1)[-1].isdigit()
        }
        result: list[GroupId] = []
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

    @staticmethod
    def _expand_to_method_level(graph: CallGraph, cluster_result: ClusterResult) -> ClusterResult:
        if len(cluster_result.clusters) >= MIN_CLUSTERS_THRESHOLD:
            return cluster_result

        clusters: dict[ClusterId, set[str]] = {}
        cluster_to_files: dict[ClusterId, set[str]] = {}
        file_to_clusters: dict[str, set[ClusterId]] = defaultdict(set)
        included: set[str] = set()

        for qualified_name, node in sorted(graph.nodes.items()):
            if node.type not in CALLABLE_TYPES:
                continue
            cluster_id = len(clusters)
            clusters[cluster_id] = {qualified_name}
            cluster_to_files[cluster_id] = {node.file_path}
            file_to_clusters[node.file_path].add(cluster_id)
            included.add(qualified_name)

        if len(clusters) < MIN_CLUSTERS_THRESHOLD:
            for qualified_name, node in sorted(graph.nodes.items()):
                if node.type not in CLASS_TYPES or qualified_name in included:
                    continue
                cluster_id = len(clusters)
                clusters[cluster_id] = {qualified_name}
                cluster_to_files[cluster_id] = {node.file_path}
                file_to_clusters[node.file_path].add(cluster_id)

        return ClusterResult(
            clusters=clusters,
            cluster_to_files=cluster_to_files,
            file_to_clusters=dict(file_to_clusters),
            strategy=METHOD_LEVEL_STRATEGY,
        )

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
