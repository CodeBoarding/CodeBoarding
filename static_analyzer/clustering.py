"""Shared clustering result and lineage models."""

from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field


ModulePath = tuple[int, ...]
ModuleMembers = dict[int, set[str]]


@dataclass
class ClusterResult:
    clusters: dict[int, set[str]] = field(default_factory=dict)
    cluster_to_files: dict[int, set[str]] = field(default_factory=dict)
    file_to_clusters: dict[str, set[int]] = field(default_factory=dict)
    strategy: str = ""

    def get_cluster_ids(self) -> set[int]:
        return set(self.clusters)

    def get_files_for_cluster(self, cluster_id: int) -> set[str]:
        return self.cluster_to_files.get(cluster_id, set())

    def get_clusters_for_file(self, file_path: str) -> set[int]:
        return self.file_to_clusters.get(file_path, set())

    def get_nodes_for_cluster(self, cluster_id: int) -> set[str]:
        return self.clusters.get(cluster_id, set())

    def visit_paths(self, fn: Callable[[str], str]) -> None:
        self.cluster_to_files = {
            cluster_id: {fn(path) for path in paths} for cluster_id, paths in self.cluster_to_files.items()
        }
        remapped: dict[str, set[int]] = defaultdict(set)
        for path, cluster_ids in self.file_to_clusters.items():
            remapped[fn(path)].update(cluster_ids)
        self.file_to_clusters = dict(remapped)


@dataclass
class InfomapClusterSnapshot:
    cluster_result: ClusterResult
    node_paths: dict[str, ModulePath] = field(default_factory=dict)
    module_members: ModuleMembers = field(default_factory=dict)
    next_cluster_id: int = 1
    codelength: float = 0.0
    level: int = 1
    graph_fingerprint: str = ""


@dataclass
class RawInfomapPartition:
    node_paths: dict[str, ModulePath]
    module_members: ModuleMembers
    codelength: float
