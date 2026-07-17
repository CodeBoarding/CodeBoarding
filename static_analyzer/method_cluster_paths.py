import threading
from collections.abc import Mapping


class MethodClusterPaths:
    """Thread-safe lineage map for qname -> scoped cluster ids."""

    def __init__(
        self,
        paths: dict[str, set[str]] | None = None,
        next_ids: dict[str, int] | None = None,
    ) -> None:
        self._paths: dict[str, set[str]] = {qname: set(cluster_ids) for qname, cluster_ids in (paths or {}).items()}
        self._next_ids = dict(next_ids or {})
        self._lock = threading.RLock()

    def __getstate__(self) -> dict[str, object]:
        with self._lock:
            return {
                "paths": self.snapshot_dict(),
                "next_ids": dict(self._next_ids),
            }

    def __setstate__(self, state: dict[str, object]) -> None:
        if "paths" in state:
            raw_paths = state.get("paths", {})
            raw_next_ids = state.get("next_ids", {})
        else:
            raw_paths = state
            raw_next_ids = {}
        if not isinstance(raw_paths, dict) or not isinstance(raw_next_ids, dict):
            raise ValueError("Invalid method-cluster lineage state")
        self._paths = {
            str(qname): {str(cluster_id) for cluster_id in cluster_ids}
            for qname, cluster_ids in raw_paths.items()
            if isinstance(cluster_ids, (set, list, tuple))
        }
        self._next_ids = {
            str(scope_id): int(next_id)
            for scope_id, next_id in raw_next_ids.items()
            if isinstance(next_id, int) and next_id > 0
        }
        self._lock = threading.RLock()

    def merge(self, other: "MethodClusterPaths") -> None:
        with self._lock:
            for qname, cluster_ids in other.snapshot():
                self._paths.setdefault(qname, set()).update(cluster_ids)
            for scope_id, next_id in other.next_ids_snapshot().items():
                self._next_ids[scope_id] = max(self._next_ids.get(scope_id, 1), next_id)

    def prune(self, surviving_nodes: Mapping[str, object]) -> "MethodClusterPaths":
        with self._lock:
            return MethodClusterPaths(
                {qname: set(cluster_ids) for qname, cluster_ids in self._paths.items() if qname in surviving_nodes},
                self._next_ids,
            )

    def scope_members(self, scope_id: str) -> dict[int, set[str]]:
        """Return the previously recorded local modules for one exact scope."""
        members: dict[int, set[str]] = {}
        with self._lock:
            for qname, cluster_ids in self._paths.items():
                for cluster_id in cluster_ids:
                    local_id = self._local_cluster_id(cluster_id, scope_id)
                    if local_id is not None:
                        members.setdefault(local_id, set()).add(qname)
        return members

    def next_cluster_id(self, scope_id: str) -> int:
        with self._lock:
            previous_ids = self.scope_members(scope_id)
            return max(self._next_ids.get(scope_id, 1), max(previous_ids, default=0) + 1)

    def reserve_cluster_ids(self, scope_id: str, cluster_ids: set[int]) -> None:
        with self._lock:
            self._next_ids[scope_id] = max(
                self._next_ids.get(scope_id, 1),
                max(cluster_ids, default=0) + 1,
            )

    def record(self, cluster_result, scope_id: str = "") -> None:
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
            self.reserve_cluster_ids(scope_id, set(cluster_result.clusters))

    def snapshot(self) -> list[tuple[str, set[str]]]:
        with self._lock:
            return [(qname, set(cluster_ids)) for qname, cluster_ids in self._paths.items()]

    def snapshot_dict(self) -> dict[str, set[str]]:
        with self._lock:
            return {qname: set(cluster_ids) for qname, cluster_ids in self._paths.items()}

    def next_ids_snapshot(self) -> dict[str, int]:
        with self._lock:
            return dict(self._next_ids)

    def _cluster_id_belongs_to_scope(self, cluster_id: str, scope_id: str) -> bool:
        if not scope_id:
            return cluster_id.isdigit()
        prefix = f"{scope_id}."
        if not cluster_id.startswith(prefix):
            return False
        return cluster_id.removeprefix(prefix).isdigit()

    def _local_cluster_id(self, cluster_id: str, scope_id: str) -> int | None:
        if not self._cluster_id_belongs_to_scope(cluster_id, scope_id):
            return None
        local_id = cluster_id if not scope_id else cluster_id.removeprefix(f"{scope_id}.")
        return int(local_id)
