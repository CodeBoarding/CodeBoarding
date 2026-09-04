"""Clustering output types."""

from __future__ import annotations

from dataclasses import dataclass, field

from clustering_ids import ClusterId, ComponentId, GroupId, ScopeId
from static_analyzer.cfg import CallGraph, CallSiteLocation


@dataclass
class ClusterResult:
    """Leaf clusters of one language, one per file, with deterministic IDs and file mappings."""

    clusters: dict[ClusterId, set[str]] = field(default_factory=dict)  # cluster_id -> node names
    cluster_to_files: dict[ClusterId, set[str]] = field(default_factory=dict)  # cluster_id -> file_paths
    file_to_clusters: dict[str, set[ClusterId]] = field(default_factory=dict)  # file_path -> cluster_ids
    strategy: str = ""  # which algorithm was used

    def get_cluster_ids(self) -> set[ClusterId]:
        return set(self.clusters.keys())

    def get_files_for_cluster(self, cluster_id: ClusterId) -> set[str]:
        return self.cluster_to_files.get(cluster_id, set())

    def get_clusters_for_file(self, file_path: str) -> set[ClusterId]:
        return self.file_to_clusters.get(file_path, set())

    def get_nodes_for_cluster(self, cluster_id: ClusterId) -> set[str]:
        return self.clusters.get(cluster_id, set())


@dataclass
class ClusterConnectionEdge:
    language: str
    source_qualified_name: str
    target_qualified_name: str
    call_sites: list[CallSiteLocation] = field(default_factory=list)


@dataclass
class GroupConnection:
    """All concrete calls from one sibling group to another."""

    source_group_id: GroupId
    target_group_id: GroupId
    edges: list[ClusterConnectionEdge] = field(default_factory=list)


@dataclass
class ClusterGroup:
    """One deterministic architectural group inside a clustered scope."""

    group_id: GroupId
    cluster_ids: list[ClusterId]
    name: str = ""
    symbol_members_by_language: dict[str, set[str]] = field(default_factory=dict)
    file_reasons: dict[str, str] = field(default_factory=dict)
    previous_component_id: ComponentId = ""
    expandable: bool = False
    children: ClusterScopeResult | None = None

    @property
    def qualified_names(self) -> set[str]:
        return {
            qualified_name
            for language_qualified_names in self.symbol_members_by_language.values()
            for qualified_name in language_qualified_names
        }


@dataclass
class ClusterScopeResult:
    """The leaf clusters, architectural groups, and communication for one graph scope."""

    scope_id: ScopeId
    graphs_by_language: dict[str, CallGraph] = field(default_factory=dict)
    leaf_clusters_by_language: dict[str, ClusterResult] = field(default_factory=dict)
    groups: list[ClusterGroup] = field(default_factory=list)
    connections: list[GroupConnection] = field(default_factory=list)
    clustering_groups: dict[GroupId, ClusterGroup] = field(default_factory=dict, init=False, repr=False)
    preclustered_scopes: dict[GroupId, ClusterScopeResult] = field(default_factory=dict, init=False, repr=False)

    def connection_between(self, source_group_id: GroupId, target_group_id: GroupId) -> GroupConnection | None:
        """Return the directed connection between two groups in this exact scope."""
        return next(
            (
                connection
                for connection in self.connections
                if connection.source_group_id == source_group_id and connection.target_group_id == target_group_id
            ),
            None,
        )

    def index_hierarchy(self) -> None:
        """Index all groups and retained child scopes in this hierarchy."""
        self.clustering_groups.clear()
        self.preclustered_scopes.clear()

        def visit(scope: ClusterScopeResult) -> None:
            for group in scope.groups:
                self.clustering_groups[group.group_id] = group
                if group.children is None:
                    continue
                self.preclustered_scopes[group.group_id] = group.children
                visit(group.children)

        visit(self)

    def register_scope(self, owner_id: GroupId, scope: ClusterScopeResult) -> None:
        """Register an exact component scope and its retained descendants."""
        scope.index_hierarchy()
        self.preclustered_scopes[owner_id] = scope
        self.clustering_groups.update(scope.clustering_groups)
        self.preclustered_scopes.update(scope.preclustered_scopes)

    def reroot_indexes(self, absorbed_ids: list[GroupId]) -> None:
        """Keep hierarchy lookups aligned with save-time single-child absorption."""
        for child_id in absorbed_ids:
            parent_id = child_id.rpartition(".")[0]
            prefix = f"{child_id}."

            def rerooted_id(group_id: GroupId) -> GroupId:
                if not group_id.startswith(prefix):
                    return group_id
                tail = group_id.removeprefix(prefix)
                return f"{parent_id}.{tail}" if parent_id else tail

            rerooted_groups: dict[GroupId, ClusterGroup] = {}
            for group_id, group in self.clustering_groups.items():
                if group_id == child_id:
                    continue
                group_id = rerooted_id(group_id)
                if group_id in rerooted_groups:
                    raise ValueError(f"Absorbing {child_id!r} creates duplicate clustering group ID {group_id!r}")
                rerooted_groups[group_id] = group

            rerooted_scopes: dict[GroupId, ClusterScopeResult] = {}
            for group_id, scope in self.preclustered_scopes.items():
                if group_id == child_id:
                    continue
                group_id = rerooted_id(group_id)
                if group_id in rerooted_scopes:
                    raise ValueError(f"Absorbing {child_id!r} creates duplicate clustering scope ID {group_id!r}")
                rerooted_scopes[group_id] = scope

            for group_id, group in rerooted_groups.items():
                group.group_id = group_id
            for scope_id, scope in rerooted_scopes.items():
                scope.scope_id = scope_id
            self.clustering_groups = rerooted_groups
            self.preclustered_scopes = rerooted_scopes

    def __post_init__(self) -> None:
        self.index_hierarchy()
