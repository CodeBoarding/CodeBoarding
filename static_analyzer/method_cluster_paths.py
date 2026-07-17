import threading
from collections.abc import Mapping


class MethodClusterPaths:
    """Thread-safe lineage map for qname -> scoped cluster ids."""

    def __init__(self, paths: dict[str, set[str]] | None = None) -> None:
        self._paths: dict[str, set[str]] = {qname: set(cluster_ids) for qname, cluster_ids in (paths or {}).items()}
        self._lock = threading.RLock()

    def __getstate__(self) -> dict[str, set[str]]:
        return self.snapshot_dict()

    def __setstate__(self, state: dict[str, set[str]]) -> None:
        self._paths = {qname: set(cluster_ids) for qname, cluster_ids in state.items()}
        self._lock = threading.RLock()

    def merge(self, other: "MethodClusterPaths") -> None:
        with self._lock:
            for qname, cluster_ids in other.snapshot():
                self._paths.setdefault(qname, set()).update(cluster_ids)

    def prune(self, surviving_nodes: Mapping[str, object]) -> "MethodClusterPaths":
        with self._lock:
            return MethodClusterPaths(
                {qname: set(cluster_ids) for qname, cluster_ids in self._paths.items() if qname in surviving_nodes}
            )

    def restore(self, baseline: Mapping[str, set[str]], surviving: set[str]) -> None:
        """Re-add a prior run's paths for methods that carried over but lost them.

        Why: an incremental rebuild drops a changed file's methods (and their paths)
        and re-adds them fresh, so a method that only moved a few lines would lose the
        sub-cluster lineage its component identity is seeded from. Only methods still
        present are restored, and only where no current path already exists.
        """
        with self._lock:
            for qname, cluster_ids in baseline.items():
                if qname in surviving and not self._paths.get(qname):
                    self._paths[qname] = set(cluster_ids)

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

    def snapshot(self) -> list[tuple[str, set[str]]]:
        with self._lock:
            return [(qname, set(cluster_ids)) for qname, cluster_ids in self._paths.items()]

    def snapshot_dict(self) -> dict[str, set[str]]:
        with self._lock:
            return {qname: set(cluster_ids) for qname, cluster_ids in self._paths.items()}

    def _cluster_id_belongs_to_scope(self, cluster_id: str, scope_id: str) -> bool:
        if not scope_id:
            return cluster_id.isdigit()
        prefix = f"{scope_id}."
        if not cluster_id.startswith(prefix):
            return False
        return cluster_id.removeprefix(prefix).isdigit()
