"""Entry point for clustering a call graph."""

from __future__ import annotations

import logging
from collections import Counter, defaultdict
from collections.abc import Callable, Collection, Mapping
from pathlib import Path
from types import MappingProxyType
from typing import Any

import networkx as nx

from clustering_ids import ClusterId, ComponentId, GroupId, ScopeId
from constants import MIN_CLUSTERS_THRESHOLD
from repo_utils.path_utils import normalize_repo_path
from static_analyzer.cfg import CallGraph, DEFAULT_REFERENCE_KINDS
from static_analyzer.clustering.engine import cluster_graph
from static_analyzer.clustering.expansion import scope_is_separable, scope_load
from static_analyzer.clustering.grouping import (
    GroupingService,
    reindex_across_languages,
    reindex_cluster_result,
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
from static_analyzer.config import CALLABLE_TYPES, CLASS_TYPES, Language

_ROOT_SCOPE_ID: ScopeId = "root"
_EMPTY_LEAF_CLUSTERS: Mapping[str, ClusterResult] = MappingProxyType({})
_EMPTY_OWNERS: Mapping[ClusterId, ComponentId] = MappingProxyType({})
_EMPTY_MEMBER_OWNERS: Mapping[str, Mapping[str, ComponentId]] = MappingProxyType({})
_EMPTY_RETAINED_CLUSTER_MEMBERS: Mapping[ClusterId, Collection[str]] = MappingProxyType({})
logger = logging.getLogger(__name__)


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
    """Build deterministic clustering results and hierarchies."""

    def cluster(self, graph: CallGraph) -> ClusterResult:
        return cluster_graph(graph.to_networkx(DEFAULT_REFERENCE_KINDS), delimiter=graph.delimiter)

    def build_full_hierarchy(self, static_analysis: Any, max_depth: int) -> ClusterScopeResult:
        """Build a full hierarchy and synchronize its persisted cluster lineage."""
        root_results = self._build_leaf_clusters(static_analysis)
        hierarchy = self._build_hierarchy(static_analysis, max_depth, root_results, _unseeded_scope)
        self._record_scopes(static_analysis, hierarchy)
        return hierarchy

    def build_incremental_hierarchy(
        self,
        static_analysis: Any,
        max_depth: int,
        root_leaf_clusters: Mapping[str, ClusterResult],
        persisted_scopes: Mapping[str, Any],
        repo_dir: Path,
        artifact_dir: Path,
    ) -> ClusterScopeResult:
        """Build an anchored hierarchy from persisted ownership and cluster lineage."""
        from diagram_analysis.exceptions import IncrementalCacheMissingError

        baseline = static_analysis.incremental_base_results
        if baseline is None:
            raise IncrementalCacheMissingError(artifact_dir)

        def scope_input(scope_id: ScopeId, graphs: Mapping[str, CallGraph]) -> ClusterScopeInput:
            persisted = persisted_scopes.get(scope_id)
            if scope_id != _ROOT_SCOPE_ID and persisted is None:
                return ClusterScopeInput()
            leaf_clusters = (
                dict(root_leaf_clusters)
                if scope_id == _ROOT_SCOPE_ID
                else self._incremental_scope_partitions(
                    baseline,
                    scope_id,
                    graphs,
                    self._persisted_members(persisted, graphs, repo_dir),
                    artifact_dir,
                )
            )
            if persisted is None:
                return ClusterScopeInput(leaf_clusters_by_language=leaf_clusters)
            cluster_owner, member_owner = self._previous_ownership(persisted, leaf_clusters, scope_id, repo_dir)
            return ClusterScopeInput(
                leaf_clusters_by_language=leaf_clusters,
                previous_owner=cluster_owner,
                previous_member_owner=member_owner,
                reserved_group_ids=frozenset(c.component_id for c in persisted.components if c.component_id),
                retain_scope=True,
            )

        hierarchy = self._build_hierarchy(static_analysis, max_depth, root_leaf_clusters, scope_input)
        self._record_scopes(static_analysis, hierarchy)
        return hierarchy

    def build_scope_hierarchy(
        self,
        graphs: Mapping[str, CallGraph],
        max_depth: int,
        root_scope_id: ScopeId,
    ) -> ClusterScopeResult:
        """Build a hierarchy rooted at one existing component scope."""
        return self._cluster_hierarchy(graphs, max_depth, root_scope_id=root_scope_id)

    @staticmethod
    def _expand_to_method_level(
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

        for qualified_name in expanded_members:
            next_new_id = ClusteringService._add_expanded_symbol(
                graph,
                qualified_name,
                source_cluster_by_member,
                retained_member_by_cluster,
                next_new_id,
                clusters,
                cluster_to_files,
                file_to_clusters,
            )

        return ClusterResult(
            clusters=clusters,
            cluster_to_files=cluster_to_files,
            file_to_clusters=dict(file_to_clusters),
            strategy=METHOD_LEVEL_STRATEGY,
        )

    def _cluster_scope(
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
                language: self._expand_to_method_level(graphs[language], cluster_result)
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
            modularity = grouping.modularity
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
        self._repair_member_ownership(groups, previous_member_owner)
        groups = [group for group in groups if group.qualified_names]
        for group, group_id in zip(
            groups,
            self._allocate_group_ids(
                scope_id,
                [group.previous_component_id for group in groups],
                reserved_group_ids,
            ),
            strict=True,
        ):
            group.group_id = group_id
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

    def _cluster_hierarchy(
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
        root = self._cluster_scope(
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

    def _build_hierarchy(
        self,
        static_analysis: Any,
        max_depth: int,
        root_results: Mapping[str, ClusterResult],
        scope_input: Callable[[ScopeId, Mapping[str, CallGraph]], ClusterScopeInput],
    ) -> ClusterScopeResult:
        def seeded_input(scope_id: ScopeId, graphs: Mapping[str, CallGraph]) -> ClusterScopeInput:
            provided = scope_input(scope_id, graphs)
            if scope_id != _ROOT_SCOPE_ID:
                return provided
            return ClusterScopeInput(
                leaf_clusters_by_language=root_results,
                previous_owner=provided.previous_owner,
                previous_member_owner=provided.previous_member_owner,
                reserved_group_ids=provided.reserved_group_ids,
                retain_scope=provided.retain_scope,
            )

        return self._cluster_hierarchy(static_analysis.available_cfgs(), max_depth, seeded_input)

    def _build_leaf_clusters(self, static_analysis: Any) -> dict[str, ClusterResult]:
        results: dict[str, ClusterResult] = {}
        offset = 0
        for language in static_analysis.get_languages():
            result = self.cluster(static_analysis.get_cfg(language))
            if offset:
                result = reindex_cluster_result(result, offset)
                logger.info("[Cluster] %s: offset IDs by +%d (%d clusters)", language, offset, len(result.clusters))
            results[str(language)] = result
            offset += max(result.clusters, default=0) + 1
        for language, result in results.items():
            try:
                static_analysis.get_clusters(Language(language)).adopt(result)
            except ValueError:
                logger.warning("Could not sync cluster cache for unknown language %s", language)
        return results

    def _incremental_scope_partitions(
        self,
        baseline: Any,
        scope_id: ScopeId,
        graphs: Mapping[str, CallGraph],
        persisted_members: Mapping[str, set[str]],
        artifact_dir: Path,
    ) -> dict[str, ClusterResult]:
        # Imported after static-analysis models initialize; cluster_delta reads those models.
        from diagram_analysis.cluster_delta import _delta_for_language
        from diagram_analysis.exceptions import IncrementalCacheMissingError

        cluster_caches = {}
        for language in baseline.get_languages():
            try:
                cluster_caches[str(language)] = baseline.get_clusters(language)
            except (KeyError, ValueError):
                continue
        for language in graphs:
            if language not in cluster_caches:
                try:
                    cluster_caches[language] = baseline.get_clusters(Language(language))
                except (KeyError, ValueError):
                    pass
        method_paths = {language: cache.method_paths for language, cache in cluster_caches.items()}
        unclustered_members = {
            language: cache.get_unclustered_members(scope_id) for language, cache in cluster_caches.items()
        }
        prefix = f"{scope_id}."
        reserved = {
            int(local_id)
            for paths in method_paths.values()
            for _name, cluster_ids in paths.snapshot()
            for cluster_id in cluster_ids
            if cluster_id.startswith(prefix) and (local_id := cluster_id.removeprefix(prefix)).isdigit()
        }
        snapshots = {
            language: self._scoped_snapshot(graph, method_paths.get(language), scope_id)
            for language, graph in graphs.items()
        }
        for language, graph in graphs.items():
            covered = {member for entry in snapshots[language].values() for member in entry.members}
            missing = (
                (persisted_members.get(language, set()) & set(graph.nodes))
                - covered
                - unclustered_members.get(language, set())
            )
            if missing:
                raise IncrementalCacheMissingError(
                    artifact_dir,
                    f"persisted scope {scope_id!r} has no cluster lineage for {language} member(s): "
                    + ", ".join(sorted(missing)[:5]),
                )
        partitions: dict[str, ClusterResult] = {}
        next_new_id = max(reserved, default=-1) + 1
        for language in sorted(graphs):
            graph = graphs[language]
            snapshot = snapshots[language]
            partition = (
                _delta_for_language(
                    language,
                    graph.to_networkx(DEFAULT_REFERENCE_KINDS),
                    snapshot,
                    next_new_id=next_new_id,
                    known_unclustered_members=unclustered_members.get(language, set()),
                ).cluster_results
                if snapshot
                else self.cluster(graph)
            )
            partition = self._expand_to_method_level(
                graph,
                partition,
                next_new_id=next_new_id if snapshot else 0,
                retained_members_by_cluster={cluster_id: entry.members for cluster_id, entry in snapshot.items()},
            )
            if not snapshot and next_new_id:
                partition = reindex_cluster_result(partition, next_new_id)
            partitions[language] = partition
            next_new_id = max(next_new_id, max(partition.clusters, default=0) + 1)
        reindex_across_languages(partitions)
        return partitions

    @staticmethod
    def _scoped_snapshot(graph: CallGraph, method_paths: Any, scope_id: ScopeId) -> dict[int, Any]:
        from diagram_analysis.cluster_snapshot import ClusterSnapshotEntry

        if method_paths is None:
            return {}
        prefix = f"{scope_id}."
        entries: dict[int, Any] = {}
        for name, cluster_ids in method_paths.snapshot():
            if name not in graph.nodes:
                continue
            for cluster_id in cluster_ids:
                local_id = cluster_id.removeprefix(prefix)
                if not cluster_id.startswith(prefix) or not local_id.isdigit():
                    continue
                entry = entries.setdefault(int(local_id), ClusterSnapshotEntry())
                entry.members.add(name)
                if file_path := graph.nodes[name].file_path:
                    entry.files.add(file_path)
                    entry.member_files[name] = file_path
        return entries

    @staticmethod
    def _persisted_members(persisted, graphs: Mapping[str, CallGraph], repo_dir: Path) -> dict[str, set[str]]:
        if persisted is None:
            return {}
        return {
            language: {
                method.qualified_name
                for component in persisted.components
                for group in component.file_methods
                if normalize_repo_path(group.file_path, repo_dir)
                in {normalize_repo_path(node.file_path, repo_dir) for node in graph.nodes.values()}
                for method in group.methods
            }
            for language, graph in graphs.items()
        }

    @staticmethod
    def _previous_ownership(scope, results: Mapping[str, ClusterResult], scope_id: ScopeId, repo_dir: Path):
        """Recover persisted ownership from stable members before local cluster IDs."""
        prefix = "" if scope_id == _ROOT_SCOPE_ID else scope_id
        claimed = {
            cluster_id: component.component_id
            for component in scope.components
            if component.component_id
            for cluster_id in component.source_cluster_ids
        }
        owners: dict[int, str] = {}
        member_owners: dict[str, dict[str, str]] = {}
        for language, result in results.items():
            files = {
                normalize_repo_path(path, repo_dir) for paths in result.cluster_to_files.values() for path in paths
            }
            by_member = {
                method.qualified_name: component.component_id
                for component in scope.components
                if component.component_id
                for group in component.file_methods
                if not files or normalize_repo_path(group.file_path, repo_dir) in files
                for method in group.methods
            }
            member_owners[language] = by_member
            for cluster_id, members in result.clusters.items():
                tally = Counter(by_member[member] for member in members if member in by_member)
                if tally:
                    owners[cluster_id] = min(tally.items(), key=lambda claim: (-claim[1], claim[0]))[0]
                    continue
                qualified = f"{prefix}.{cluster_id}" if prefix else str(cluster_id)
                if qualified in claimed:
                    owners[cluster_id] = claimed[qualified]
        return owners, member_owners

    @staticmethod
    def _record_scopes(static_analysis: Any, scope: ClusterScopeResult) -> None:
        for language, partition in scope.leaf_clusters_by_language.items():
            cache = static_analysis.get_clusters(Language(language))
            cache_scope_id = "" if scope.scope_id == _ROOT_SCOPE_ID else scope.scope_id
            if scope.scope_id == _ROOT_SCOPE_ID:
                cache.adopt(partition)
            else:
                cache.record_scope(partition, scope.scope_id)
            assigned_members = {
                member for group in scope.groups for member in group.symbol_members_by_language.get(language, set())
            }
            clustered_members = {member for members in partition.clusters.values() for member in members}
            cache.record_unclustered(assigned_members - clustered_members, cache_scope_id)
        for group in scope.groups:
            if group.children is None:
                continue
            ClusteringService._record_scopes(static_analysis, group.children)

    @staticmethod
    def _add_expanded_symbol(
        graph: CallGraph,
        name: str,
        source_by_member: Mapping[str, ClusterId],
        retained_by_cluster: Mapping[ClusterId, str],
        next_new_id: ClusterId,
        clusters: dict[int, set[str]],
        cluster_to_files: dict[int, set[str]],
        file_to_clusters: dict[str, set[int]],
    ) -> ClusterId:
        source_id = source_by_member.get(name)
        cluster_id = source_id if source_id is not None and retained_by_cluster.get(source_id) == name else next_new_id
        if cluster_id == next_new_id:
            next_new_id += 1
        file_path = graph.nodes[name].file_path
        clusters[cluster_id] = {name}
        cluster_to_files[cluster_id] = {file_path}
        file_to_clusters[file_path].add(cluster_id)
        return next_new_id

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
            child = self._cluster_scope(
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
                    leaf_clusters_by_language=child.leaf_clusters_by_language,
                    modularity=child.unanchored_modularity,
                    load=load,
                    method_count=method_count,
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
    def _repair_member_ownership(
        groups: list[ClusterGroup],
        previous_member_owner: Mapping[str, Mapping[str, ComponentId]],
    ) -> None:
        """Keep surviving members with their surviving previous group."""
        group_by_id = {group.group_id: group for group in groups}
        for language, owner_by_member in previous_member_owner.items():
            current_group = {
                qualified_name: group
                for group in groups
                for qualified_name in group.symbol_members_by_language.get(language, set())
            }
            missing_owners = {
                owner_by_member[qualified_name] for qualified_name in current_group if qualified_name in owner_by_member
            } - set(group_by_id)
            for previous_group_id in sorted(missing_owners):
                target = ClusterGroup(
                    group_id=previous_group_id,
                    cluster_ids=[],
                    previous_component_id=previous_group_id,
                )
                groups.append(target)
                group_by_id[previous_group_id] = target
            for qualified_name, previous_group_id in owner_by_member.items():
                source = current_group.get(qualified_name)
                target = group_by_id.get(previous_group_id)
                if source is None or target is None or source is target:
                    continue
                source.symbol_members_by_language[language].remove(qualified_name)
                if not source.symbol_members_by_language[language]:
                    del source.symbol_members_by_language[language]
                target.symbol_members_by_language.setdefault(language, set()).add(qualified_name)

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
