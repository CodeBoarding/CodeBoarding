"""Clustering output types."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Hashable, Mapping
from dataclasses import dataclass, field

from static_analyzer.node import Node

# Marker on a ClusterResult whose clusters are synthetic one-method-per-cluster
# groups, produced when a subgraph had too few natural clusters to assign methods
# at a useful granularity. Its modularity is not comparable to a real clustering's.
METHOD_LEVEL_STRATEGY = "method_level_expansion"


@dataclass
class ClusterResult:
    """A partition of a CallGraph. Provides deterministic cluster IDs and file mappings."""

    clusters: dict[int, set[str]] = field(default_factory=dict)  # cluster_id -> node names
    cluster_to_files: dict[int, set[str]] = field(default_factory=dict)  # cluster_id -> file_paths
    file_to_clusters: dict[str, set[int]] = field(default_factory=dict)  # file_path -> cluster_ids
    strategy: str = ""  # which algorithm was used

    def get_cluster_ids(self) -> set[int]:
        return set(self.clusters.keys())

    def get_files_for_cluster(self, cluster_id: int) -> set[str]:
        return self.cluster_to_files.get(cluster_id, set())

    def get_clusters_for_file(self, file_path: str) -> set[int]:
        return self.file_to_clusters.get(file_path, set())

    def get_nodes_for_cluster(self, cluster_id: int) -> set[str]:
        return self.clusters.get(cluster_id, set())

    def visit_paths(self, fn: Callable[[str], str]) -> None:
        self.cluster_to_files = {cid: {fn(path) for path in paths} for cid, paths in self.cluster_to_files.items()}
        remapped_file_to_clusters: dict[str, set[int]] = defaultdict(set)
        for path, cluster_ids in self.file_to_clusters.items():
            remapped_file_to_clusters[fn(path)].update(cluster_ids)
        self.file_to_clusters = dict(remapped_file_to_clusters)

    def select(self, surviving_nodes: Mapping[str, Node]) -> ClusterResult:
        """Return a copy keeping only qnames in ``surviving_nodes``, with file mappings recomputed."""
        kept_clusters: dict[int, set[str]] = {}
        kept_cluster_to_files: dict[int, set[str]] = {}
        kept_file_to_clusters: dict[str, set[int]] = {}
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


@dataclass
class ClusterConnectionEdge:
    """One concrete call crossing between two structural groups."""

    language: str
    source: str
    target: str
    call_sites: list[dict[str, Hashable]] = field(default_factory=list)


@dataclass
class ClusterConnection:
    """All concrete calls from one sibling group to another."""

    source_group_id: str
    target_group_id: str
    edges: list[ClusterConnectionEdge] = field(default_factory=list)


@dataclass
class ClusterScopeInput:
    """Optional precomputed partition and ownership anchors for one scope."""

    partitions: Mapping[str, ClusterResult] = field(default_factory=dict)
    previous_owner: Mapping[int, str] = field(default_factory=dict)


@dataclass
class ClusterGroup:
    """One deterministic architectural group inside a clustered scope."""

    group_id: str
    cluster_ids: list[int]
    members: dict[str, set[str]] = field(default_factory=dict)
    previous_component_id: str = ""
    children: ClusterScopeResult | None = None

    @property
    def qualified_names(self) -> set[str]:
        return {qualified_name for language_members in self.members.values() for qualified_name in language_members}


@dataclass
class ClusterScopeResult:
    """The complete partition, grouping, and communication for one graph scope."""

    scope_id: str
    partitions: dict[str, ClusterResult] = field(default_factory=dict)
    groups: list[ClusterGroup] = field(default_factory=list)
    connections: list[ClusterConnection] = field(default_factory=list)
    modularity: float = 0.0
    fresh_modularity: float = 0.0
    regrouped: bool = False
