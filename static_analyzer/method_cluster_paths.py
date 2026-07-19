import threading
from collections.abc import Mapping

from static_analyzer.clustering import ClusterResult


class MethodClusterPaths:
    """Thread-safe lineage map for qname -> scoped cluster ids."""

    def __init__(
        self,
        paths: dict[str, set[str]] | None = None,
        next_cluster_ids: dict[str, int] | None = None,
    ) -> None:
        self._paths: dict[str, set[str]] = {qname: set(cluster_ids) for qname, cluster_ids in (paths or {}).items()}
        self._next_cluster_ids = dict(next_cluster_ids or {})
        self._lock = threading.RLock()

    def __getstate__(self) -> tuple[dict[str, set[str]], dict[str, int]]:
        return self.snapshot_dict(), self.snapshot_next_cluster_ids()

    def __setstate__(
        self,
        state: tuple[dict[str, set[str]], dict[str, int]] | dict[str, set[str]],
    ) -> None:
        paths, next_cluster_ids = state if isinstance(state, tuple) else (state, {})
        self._paths = {qname: set(cluster_ids) for qname, cluster_ids in paths.items()}
        self._next_cluster_ids = dict(next_cluster_ids)
        self._lock = threading.RLock()

    def merge(self, other: "MethodClusterPaths") -> None:
        with self._lock:
            for qname, cluster_ids in other.snapshot():
                self._paths.setdefault(qname, set()).update(cluster_ids)
            for scope_id, next_cluster_id in other.snapshot_next_cluster_ids().items():
                self._next_cluster_ids[scope_id] = max(
                    self._next_cluster_ids.get(scope_id, 0),
                    next_cluster_id,
                )

    def prune(self, surviving_nodes: Mapping[str, object]) -> "MethodClusterPaths":
        with self._lock:
            return MethodClusterPaths(
                {qname: set(cluster_ids) for qname, cluster_ids in self._paths.items() if qname in surviving_nodes},
                self._next_cluster_ids,
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
            self._next_cluster_ids[scope_id] = max(
                self._next_cluster_ids.get(scope_id, 0),
                max(cluster_result.clusters, default=-1) + 1,
            )

    def snapshot(self) -> list[tuple[str, set[str]]]:
        with self._lock:
            return [(qname, set(cluster_ids)) for qname, cluster_ids in self._paths.items()]

    def snapshot_dict(self) -> dict[str, set[str]]:
        with self._lock:
            return {qname: set(cluster_ids) for qname, cluster_ids in self._paths.items()}

    def snapshot_next_cluster_ids(self) -> dict[str, int]:
        with self._lock:
            return dict(self._next_cluster_ids)

    def next_cluster_id(self, scope_id: str) -> int:
        """Return a non-recycled scoped ID, including legacy path-only state."""
        with self._lock:
            next_cluster_id = self._next_cluster_ids.get(scope_id, 0)
            prefix = f"{scope_id}." if scope_id else ""
            for cluster_ids in self._paths.values():
                for cluster_id in cluster_ids:
                    if not self._cluster_id_belongs_to_scope(cluster_id, scope_id):
                        continue
                    local_id = cluster_id.removeprefix(prefix)
                    next_cluster_id = max(next_cluster_id, int(local_id) + 1)
            return next_cluster_id

    def _cluster_id_belongs_to_scope(self, cluster_id: str, scope_id: str) -> bool:
        if not scope_id:
            return cluster_id.isdigit()
        prefix = f"{scope_id}."
        if not cluster_id.startswith(prefix):
            return False
        return cluster_id.removeprefix(prefix).isdigit()
