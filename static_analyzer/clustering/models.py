"""Clustering output types."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field

from clustering_ids import ClusterId, ComponentId, GroupId, ScopeId
from static_analyzer.cfg.edge import CallSiteLocation
from static_analyzer.node import Node

# Marker on a ClusterResult whose clusters are synthetic one-method-per-cluster
# groups, produced when a subgraph had too few natural clusters to assign methods
# at a useful granularity. Its modularity is not comparable to a real clustering's.
METHOD_LEVEL_STRATEGY = "method_level_expansion"


@dataclass
class ClusterResult:
    """Leaf clusters from one language's call graph, with deterministic IDs and file mappings."""

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

    def visit_paths(self, fn: Callable[[str], str]) -> None:
        self.cluster_to_files = {cid: {fn(path) for path in paths} for cid, paths in self.cluster_to_files.items()}
        remapped_file_to_clusters: dict[str, set[ClusterId]] = defaultdict(set)
        for path, cluster_ids in self.file_to_clusters.items():
            remapped_file_to_clusters[fn(path)].update(cluster_ids)
        self.file_to_clusters = dict(remapped_file_to_clusters)

    def select(self, surviving_nodes: Mapping[str, Node]) -> ClusterResult:
        """Return a copy keeping only qnames in ``surviving_nodes``, with file mappings recomputed."""
        kept_clusters: dict[ClusterId, set[str]] = {}
        kept_cluster_to_files: dict[ClusterId, set[str]] = {}
        kept_file_to_clusters: dict[str, set[ClusterId]] = {}
        for cid, members in self.clusters.items():
            kept = {m for m in members if m in surviving_nodes}
            if not kept:
                continue
            kept_clusters[cid] = kept
            files: set[str] = set()
            for qname in kept:
                file_path = surviving_nodes[qname].file_path
                if file_path:
                    files.add(file_path)
                    kept_file_to_clusters.setdefault(file_path, set()).add(cid)
            if files:
                kept_cluster_to_files[cid] = files
        return ClusterResult(
            clusters=kept_clusters,
            cluster_to_files=kept_cluster_to_files,
            file_to_clusters=kept_file_to_clusters,
            strategy=self.strategy,
        )


@dataclass(frozen=True)
class AnchoredGrouping:
    """A grouping carried forward from the previous run."""

    groups: list[set[ClusterId]]
    owners: list[ComponentId]
    regrouped: bool


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
    symbol_members_by_language: dict[str, set[str]] = field(default_factory=dict)
    previous_component_id: ComponentId = ""
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
    leaf_clusters_by_language: dict[str, ClusterResult] = field(default_factory=dict)
    groups: list[ClusterGroup] = field(default_factory=list)
    connections: list[GroupConnection] = field(default_factory=list)
    modularity: float = 0.0
    regrouped: bool = False
