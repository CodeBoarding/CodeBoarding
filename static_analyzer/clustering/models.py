"""Clustering output types."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Mapping
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

    def prune(self, surviving_nodes: Mapping[str, Node]) -> ClusterResult:
        """Drop qnames not in ``surviving_nodes`` and recompute the file mappings."""
        pruned_clusters: dict[int, set[str]] = {}
        pruned_cluster_to_files: dict[int, set[str]] = {}
        pruned_file_to_clusters: dict[str, set[int]] = {}
        for cid, members in self.clusters.items():
            kept = {m for m in members if m in surviving_nodes}
            if not kept:
                continue
            pruned_clusters[cid] = kept
            files: set[str] = set()
            for qname in kept:
                file_path = surviving_nodes[qname].file_path
                if file_path:
                    files.add(file_path)
                    pruned_file_to_clusters.setdefault(file_path, set()).add(cid)
            if files:
                pruned_cluster_to_files[cid] = files
        return ClusterResult(
            clusters=pruned_clusters,
            cluster_to_files=pruned_cluster_to_files,
            file_to_clusters=pruned_file_to_clusters,
            strategy=self.strategy,
        )
