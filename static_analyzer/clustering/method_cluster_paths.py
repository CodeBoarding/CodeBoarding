from __future__ import annotations

import threading
from collections.abc import Container, Mapping
from types import MappingProxyType

from static_analyzer.clustering.models import ClusterResult

_EMPTY_PATHS: Mapping[str, set[str]] = MappingProxyType({})


class MethodClusterPaths:
    """Thread-safe lineage map for qname -> scoped cluster ids."""

    def __init__(self, paths: Mapping[str, set[str]] = _EMPTY_PATHS) -> None:
        self._paths: dict[str, set[str]] = {qname: set(cluster_ids) for qname, cluster_ids in paths.items()}
        self._lock = threading.RLock()

    def __getstate__(self) -> dict[str, set[str]]:
        return self.snapshot_dict()

    def __setstate__(self, state: dict[str, set[str]]) -> None:
        self._paths = {qname: set(cluster_ids) for qname, cluster_ids in state.items()}
        self._lock = threading.RLock()

    def merge(self, other: MethodClusterPaths) -> None:
        with self._lock:
            for qname, cluster_ids in other.snapshot():
                self._paths.setdefault(qname, set()).update(cluster_ids)

    def select(self, surviving_nodes: Container[str]) -> MethodClusterPaths:
        with self._lock:
            return MethodClusterPaths(
                {qname: set(cluster_ids) for qname, cluster_ids in self._paths.items() if qname in surviving_nodes}
            )

    def record(self, cluster_result: ClusterResult, scope_id: str = "") -> None:
        prefix = f"{scope_id}." if scope_id else ""
        with self._lock:
            for existing in self._paths.values():
                existing -= {
                    cluster_id for cluster_id in existing if self._cluster_id_belongs_to_scope(cluster_id, scope_id)
                }
            for cluster_id, members in cluster_result.clusters.items():
                qualified_cluster_id = f"{prefix}{cluster_id}"
                for member in members:
                    self._paths.setdefault(member, set()).add(qualified_cluster_id)

    def reroot_scope(self, child_id: str, parent_id: str) -> None:
        """Move child lineage onto its parent and replace the parent's partition."""

        with self._lock:
            for qname, cluster_ids in self._paths.items():
                kept: set[str] = set()
                for cluster_id in cluster_ids:
                    scope_id, _, local = cluster_id.rpartition(".")
                    if not local.isdigit():
                        kept.add(cluster_id)
                    elif self._scope_belongs_to(scope_id, child_id):
                        moved = f"{parent_id}{scope_id[len(child_id) :]}" if parent_id else scope_id[len(child_id) :]
                        kept.add(f"{moved.lstrip('.')}.{local}".lstrip("."))
                    elif not self._scope_belongs_to(scope_id, parent_id):
                        kept.add(cluster_id)
                self._paths[qname] = kept

    def snapshot(self) -> list[tuple[str, set[str]]]:
        with self._lock:
            return [(qname, set(cluster_ids)) for qname, cluster_ids in self._paths.items()]

    def snapshot_dict(self) -> dict[str, set[str]]:
        with self._lock:
            return {qname: set(cluster_ids) for qname, cluster_ids in self._paths.items()}

    @staticmethod
    def _scope_belongs_to(scope_id: str, root: str) -> bool:
        return scope_id == root or scope_id.startswith(f"{root}.")

    def _cluster_id_belongs_to_scope(self, cluster_id: str, scope_id: str) -> bool:
        if not scope_id:
            return cluster_id.isdigit()
        prefix = f"{scope_id}."
        if not cluster_id.startswith(prefix):
            return False
        return cluster_id.removeprefix(prefix).isdigit()
