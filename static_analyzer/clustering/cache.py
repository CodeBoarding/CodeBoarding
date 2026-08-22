"""Per-language clustering state, owned by ``LanguageResults``.

Kept off ``CallGraph`` so the graph stays pure structure: a partition is a
*result* about a graph, not part of it, and the same graph can be re-clustered
at a different scope without disturbing what an earlier run recorded.
"""

from __future__ import annotations

from collections.abc import Callable, Collection, Mapping
from dataclasses import dataclass, field

from static_analyzer.clustering.method_cluster_paths import MethodClusterPaths
from static_analyzer.clustering.models import ClusterResult
from static_analyzer.node import Node


@dataclass
class ClusterCache:
    """A language's structural partitions and explicitly unclustered group members."""

    result: ClusterResult = field(default_factory=ClusterResult)
    method_paths: MethodClusterPaths = field(default_factory=MethodClusterPaths)
    unclustered_members_by_scope: dict[str, set[str]] = field(default_factory=dict)

    def adopt(self, cluster_result: ClusterResult) -> None:
        """Make ``cluster_result`` this language's partition and record it at root scope."""
        self.result = cluster_result
        self.method_paths.record(cluster_result)

    def record_scope(self, cluster_result: ClusterResult, scope_id: str) -> None:
        """Record a nested scope's partition in the method lineage only.

        Why: sub-clustering a component produces a partition of that scope, not of
        the language — adopting it would overwrite the top-level partition that
        ``cluster_snapshot`` reads.
        """
        self.method_paths.record(cluster_result, scope_id)

    def record_unclustered(self, members: Collection[str], scope_id: str = "") -> None:
        """Record symbols assigned to a group without structural cluster lineage."""
        self.unclustered_members_by_scope[scope_id] = set(members)

    def get_unclustered_members(self, scope_id: str = "") -> set[str]:
        return set(self.unclustered_members_by_scope.get(scope_id, set()))

    def reroot_scope(self, child_id: str, parent_id: str) -> None:
        """Move a child's structural and unclustered lineage onto its parent."""
        self.method_paths.reroot_scope(child_id, parent_id)
        rerooted: dict[str, set[str]] = {}
        for scope_id, members in self.unclustered_members_by_scope.items():
            if self._scope_belongs_to(scope_id, child_id):
                moved = f"{parent_id}{scope_id[len(child_id) :]}" if parent_id else scope_id[len(child_id) :]
                rerooted[moved.lstrip(".")] = set(members)
            elif not self._scope_belongs_to(scope_id, parent_id):
                rerooted[scope_id] = set(members)
        self.unclustered_members_by_scope = rerooted

    def select(self, surviving_nodes: Mapping[str, Node]) -> ClusterCache:
        """Return a copy keeping only ``surviving_nodes``, for filter/union of the graph."""
        return ClusterCache(
            result=self.result.select(surviving_nodes),
            method_paths=self.method_paths.select(surviving_nodes),
            unclustered_members_by_scope={
                scope_id: {member for member in members if member in surviving_nodes}
                for scope_id, members in self.unclustered_members_by_scope.items()
            },
        )

    def visit_paths(self, fn: Callable[[str], str]) -> None:
        self.result.visit_paths(fn)

    @staticmethod
    def _scope_belongs_to(scope_id: str, root: str) -> bool:
        return scope_id == root or scope_id.startswith(f"{root}.")
