"""Entry point for clustering a call graph."""

from __future__ import annotations

import logging
from collections import Counter, defaultdict
from collections.abc import Callable, Collection, Mapping
from pathlib import Path
from types import MappingProxyType
from typing import Any

import networkx as nx

from clustering_ids import ROOT_SCOPE_ID, ClusterId, CodeBoardingClusterIds, ComponentId, GroupId, ScopeId
from constants import MIN_CLUSTERS_THRESHOLD
from repo_utils.path_utils import normalize_repo_path
from static_analyzer.analysis_result import StaticAnalysisResults
from static_analyzer.cfg import CallGraph, DEFAULT_REFERENCE_KINDS
from static_analyzer.clustering.cache import ClusterCache
from static_analyzer.clustering.delta import delta_for_language
from static_analyzer.clustering.exceptions import (
    IncrementalCacheMissingError,
    NamingModelUnavailableError,
    PersistedOwnershipConflictError,
)
from static_analyzer.clustering.expansion import scope_is_separable, scope_load
from static_analyzer.clustering.grouping import (
    GroupingService,
    reindex_across_languages,
    reindex_cluster_result,
)
from static_analyzer.clustering.naming import NamingModel, file_leaf_clusters
from static_analyzer.clustering.models import (
    METHOD_LEVEL_STRATEGY,
    ClusterConnectionEdge,
    ClusterGroup,
    ClusterResult,
    ClusterScopeInput,
    ClusterScopeResult,
    GroupConnection,
)
from static_analyzer.clustering.snapshot import entries_from_partition
from static_analyzer.config import CALLABLE_TYPES, CLASS_TYPES, Language

_EMPTY_LEAF_CLUSTERS: Mapping[str, ClusterResult] = MappingProxyType({})
_EMPTY_OWNERS: Mapping[ClusterId, ComponentId] = MappingProxyType({})
_EMPTY_MEMBER_OWNERS: Mapping[str, Mapping[str, ComponentId]] = MappingProxyType({})
_EMPTY_RETAINED_CLUSTER_MEMBERS: Mapping[ClusterId, Collection[str]] = MappingProxyType({})
PersistedScopeOwnership = dict[str, dict[str, ComponentId]]
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

    def __init__(self, naming_model: NamingModel, repo_root: str = "") -> None:
        if naming_model is None:
            raise NamingModelUnavailableError("ClusteringService was constructed without one")
        self._naming_model = naming_model
        self._repo_root = repo_root

    def cluster(self, graph: CallGraph) -> ClusterResult:
        """Leaves are files.

        Why not graph communities: they are drawn from the call graph and cross the
        boundaries a name partition keeps, which capped any grouping over them at 0.34 on
        the Beacon ruler against 0.94 for the same names over files.
        """
        return file_leaf_clusters(graph)

    def build_full_hierarchy(
        self,
        static_analysis: StaticAnalysisResults,
        max_depth: int,
        cluster_caches: Mapping[str, ClusterCache] | None = None,
    ) -> ClusterScopeResult:
        """Build a full hierarchy and synchronize its persisted cluster lineage."""
        root_results = self._build_leaf_clusters(static_analysis)
        hierarchy = self._build_hierarchy(static_analysis, max_depth, root_results, _unseeded_scope)
        self._record_scopes(static_analysis, hierarchy, cluster_caches)
        return hierarchy

    def build_incremental_hierarchy(
        self,
        static_analysis: StaticAnalysisResults,
        max_depth: int,
        root_leaf_clusters: Mapping[str, ClusterResult],
        persisted_scopes: Mapping[str, Any],
        repo_dir: Path,
        artifact_dir: Path,
        cluster_caches: Mapping[str, ClusterCache] | None = None,
    ) -> ClusterScopeResult:
        """Build an anchored hierarchy from persisted ownership and cluster lineage."""
        baseline = static_analysis.incremental_base_results
        if baseline is None:
            raise IncrementalCacheMissingError(artifact_dir)
        ownership_by_scope = self._index_persisted_ownership(
            persisted_scopes,
            static_analysis.available_cfgs(),
            repo_dir,
        )

        def scope_input(scope_id: ScopeId, graphs: Mapping[str, CallGraph]) -> ClusterScopeInput:
            persisted = persisted_scopes.get(scope_id)
            if scope_id != ROOT_SCOPE_ID and persisted is None:
                return ClusterScopeInput()
            leaf_clusters = (
                dict(root_leaf_clusters)
                if scope_id == ROOT_SCOPE_ID
                else self._incremental_scope_partitions(
                    baseline,
                    scope_id,
                    graphs,
                    ownership_by_scope.get(scope_id, {}),
                    artifact_dir,
                )
            )
            if persisted is None:
                return ClusterScopeInput(leaf_clusters_by_language=leaf_clusters)
            member_owner = ownership_by_scope.get(scope_id, {})
            cluster_owner = self._previous_cluster_ownership(persisted, leaf_clusters, scope_id, member_owner)
            return ClusterScopeInput(
                leaf_clusters_by_language=leaf_clusters,
                previous_owner=cluster_owner,
                previous_member_owner=member_owner,
                reserved_group_ids=frozenset(c.component_id for c in persisted.components if c.component_id),
                retain_scope=True,
            )

        hierarchy = self._build_hierarchy(static_analysis, max_depth, root_leaf_clusters, scope_input)
        self._record_scopes(static_analysis, hierarchy, cluster_caches)
        return hierarchy

    def build_scope_hierarchy(
        self,
        static_analysis: StaticAnalysisResults,
        graphs: Mapping[str, CallGraph],
        max_depth: int,
        root_scope_id: ScopeId,
        cluster_caches: Mapping[str, ClusterCache] | None = None,
    ) -> ClusterScopeResult:
        """Build one existing component scope and synchronize its persisted lineage."""
        hierarchy = self._cluster_hierarchy(graphs, max_depth, root_scope_id=root_scope_id)
        self._record_scopes(static_analysis, hierarchy, cluster_caches)
        return hierarchy

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
        scope_id: ScopeId = ROOT_SCOPE_ID,
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
        grouping_service = GroupingService(self._naming_model, self._repo_root)
        subcomponents = scope_id != ROOT_SCOPE_ID
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
            unanchored_group_count = grouping.unanchored_group_count
            regrouped = grouping.regrouped
        else:
            raw_groups, modularity = grouping_service.group(
                scope_leaf_clusters,
                nx_graphs,
                subcomponents=subcomponents,
            )
            owners = [""] * len(raw_groups)
            unanchored_modularity = modularity
            unanchored_group_count = len(raw_groups)
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
            unanchored_group_count=unanchored_group_count,
            regrouped=regrouped,
        )

    def _cluster_hierarchy(
        self,
        graphs: Mapping[str, CallGraph],
        max_depth: int,
        scope_input: Callable[[ScopeId, Mapping[str, CallGraph]], ClusterScopeInput] = _unseeded_scope,
        root_scope_id: ScopeId = ROOT_SCOPE_ID,
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
            method_level_fallback=root_scope_id != ROOT_SCOPE_ID,
        )
        self._cluster_children(root, graphs, 1, max_depth, scope_input)
        root.index_hierarchy()
        return root

    def _build_hierarchy(
        self,
        static_analysis: StaticAnalysisResults,
        max_depth: int,
        root_results: Mapping[str, ClusterResult],
        scope_input: Callable[[ScopeId, Mapping[str, CallGraph]], ClusterScopeInput],
    ) -> ClusterScopeResult:
        def seeded_input(scope_id: ScopeId, graphs: Mapping[str, CallGraph]) -> ClusterScopeInput:
            provided = scope_input(scope_id, graphs)
            if scope_id != ROOT_SCOPE_ID:
                return provided
            return ClusterScopeInput(
                leaf_clusters_by_language=root_results,
                previous_owner=provided.previous_owner,
                previous_member_owner=provided.previous_member_owner,
                reserved_group_ids=provided.reserved_group_ids,
                retain_scope=provided.retain_scope,
            )

        return self._cluster_hierarchy(static_analysis.available_cfgs(), max_depth, seeded_input)

    def _build_leaf_clusters(self, static_analysis: StaticAnalysisResults) -> dict[str, ClusterResult]:
        results: dict[str, ClusterResult] = {}
        offset = 0
        for language in static_analysis.get_languages():
            result = self.cluster(static_analysis.get_cfg(language))
            if offset:
                result = reindex_cluster_result(result, offset)
                logger.info("[Cluster] %s: offset IDs by +%d (%d clusters)", language, offset, len(result.clusters))
            results[str(language)] = result
            offset += max(result.clusters, default=0) + 1
        return results

    def _incremental_scope_partitions(
        self,
        baseline: StaticAnalysisResults,
        scope_id: ScopeId,
        graphs: Mapping[str, CallGraph],
        persisted_ownership: Mapping[str, Mapping[str, ComponentId]],
        artifact_dir: Path,
    ) -> dict[str, ClusterResult]:
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
        unclustered_members = {
            language: cache.get_unclustered_members(scope_id) for language, cache in cluster_caches.items()
        }
        reserved = {
            cluster_id for cache in cluster_caches.values() for cluster_id in cache.get_partition(scope_id).clusters
        }
        snapshots = {
            language: entries_from_partition(
                cluster_caches[language].get_partition(scope_id) if language in cluster_caches else ClusterResult(),
                graph.to_networkx(reference_kinds=()),
            )
            for language, graph in graphs.items()
        }
        for language, graph in graphs.items():
            covered = {member for entry in snapshots[language].values() for member in entry.members}
            missing = (
                (set(persisted_ownership.get(language, {})) & set(graph.nodes))
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
                delta_for_language(
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
    def _index_persisted_ownership(
        persisted_scopes: Mapping[ScopeId, Any],
        graphs: Mapping[str, CallGraph],
        repo_dir: Path,
    ) -> dict[ScopeId, PersistedScopeOwnership]:
        """Index live persisted callable/class ownership once for every hierarchy scope."""
        live_by_language_file: dict[str, dict[str, set[str]]] = {}
        for language, graph in graphs.items():
            by_file: dict[str, set[str]] = defaultdict(set)
            for qualified_name, node in graph.nodes.items():
                if node.type not in CALLABLE_TYPES | CLASS_TYPES:
                    continue
                by_file[normalize_repo_path(node.file_path, repo_dir)].add(qualified_name)
            live_by_language_file[language] = dict(by_file)

        indexed: dict[ScopeId, PersistedScopeOwnership] = {}
        for scope_id, scope in persisted_scopes.items():
            scope_owners: PersistedScopeOwnership = {}
            for component in scope.components:
                if not component.component_id:
                    continue
                for group in component.file_methods:
                    normalized_path = normalize_repo_path(group.file_path, repo_dir)
                    for language, live_by_file in live_by_language_file.items():
                        live_members = live_by_file.get(normalized_path, set())
                        if not live_members:
                            continue
                        language_owners = scope_owners.setdefault(language, {})
                        for method in group.methods:
                            qualified_name = method.qualified_name
                            if qualified_name not in live_members:
                                continue
                            owner = language_owners.get(qualified_name)
                            if owner and owner != component.component_id:
                                raise PersistedOwnershipConflictError(
                                    scope_id,
                                    language,
                                    qualified_name,
                                    {owner, component.component_id},
                                )
                            language_owners[qualified_name] = component.component_id
            indexed[scope_id] = scope_owners
        return indexed

    @staticmethod
    def _previous_cluster_ownership(
        scope,
        results: Mapping[str, ClusterResult],
        scope_id: ScopeId,
        member_owners: Mapping[str, Mapping[str, ComponentId]],
    ) -> dict[ClusterId, ComponentId]:
        """Recover structural cluster ownership from the persisted member index."""
        prefix = CodeBoardingClusterIds.prefix_for_scope(scope_id)
        claimed = {
            cluster_id: component.component_id
            for component in scope.components
            if component.component_id
            for cluster_id in component.source_cluster_ids
        }
        owners: dict[int, str] = {}
        for language, result in results.items():
            by_member = member_owners.get(language, {})
            for cluster_id, members in result.clusters.items():
                tally = Counter(by_member[member] for member in members if member in by_member)
                if tally:
                    owners[cluster_id] = min(tally.items(), key=lambda claim: (-claim[1], claim[0]))[0]
                    continue
                qualified = CodeBoardingClusterIds.qualify_local_id(str(cluster_id), prefix)
                if qualified in claimed:
                    owners[cluster_id] = claimed[qualified]
        return owners

    @staticmethod
    def _record_scopes(
        static_analysis: StaticAnalysisResults,
        scope: ClusterScopeResult,
        cluster_caches: Mapping[str, ClusterCache] | None = None,
    ) -> None:
        for language, partition in scope.leaf_clusters_by_language.items():
            cache = (
                cluster_caches[language]
                if cluster_caches is not None
                else static_analysis.get_clusters(Language(language))
            )
            cache_scope_id = CodeBoardingClusterIds.prefix_for_scope(scope.scope_id)
            assigned_members = {
                member for group in scope.groups for member in group.symbol_members_by_language.get(language, set())
            }
            clustered_members = {member for members in partition.clusters.values() for member in members}
            cache.record_scope(partition, assigned_members - clustered_members, cache_scope_id)
        for group in scope.groups:
            if group.children is None:
                continue
            ClusteringService._record_scopes(static_analysis, group.children, cluster_caches)

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
            load = scope_load(method_count, file_count)
            if (
                not child_input.retain_scope
                and load < 1.0
                and (
                    child.unanchored_group_count < 2
                    or not scope_is_separable(
                        leaf_clusters_by_language=child.leaf_clusters_by_language,
                        modularity=child.unanchored_modularity,
                        load=load,
                        method_count=method_count,
                    )
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
        prefix = CodeBoardingClusterIds.prefix_for_scope(scope_id)
        used_indices = [
            int(local_id)
            for group_id in allocated
            if (local_id := group_id.removeprefix(f"{prefix}." if prefix else "")).isdigit()
            and (not prefix or group_id.startswith(f"{prefix}."))
        ]
        next_index = max(used_indices, default=0) + 1
        for owner in owners:
            if owner:
                result.append(owner)
                continue
            while True:
                candidate = CodeBoardingClusterIds.qualify_local_id(str(next_index), prefix)
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
        unowned_cluster_ids = {
            cluster_id
            for cluster_result in leaf_clusters_by_language.values()
            for cluster_id in cluster_result.clusters
            if cluster_id not in group_by_cluster
        }
        assert not unowned_cluster_ids, f"Leaf clusters have no group owner: {sorted(unowned_cluster_ids)}"
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
