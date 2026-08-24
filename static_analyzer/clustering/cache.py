"""Per-language clustering state, owned by ``LanguageResults``.

Kept off ``CallGraph`` so the graph stays pure structure: a partition is a
*result* about a graph, not part of it, and the same graph can be re-clustered
at a different scope without disturbing what an earlier run recorded.
"""

from __future__ import annotations

import copy
from collections.abc import Callable, Collection, Mapping
from dataclasses import dataclass, field

from static_analyzer.clustering.models import ClusterResult
from static_analyzer.node import Node


@dataclass
class ClusterScopeLineage:
    """One scope's structural partition and group-owned members omitted from it."""

    partition: ClusterResult = field(default_factory=ClusterResult)
    unclustered_members: set[str] = field(default_factory=set)

    def select(self, surviving_nodes: Mapping[str, Node]) -> ClusterScopeLineage:
        return ClusterScopeLineage(
            partition=self.partition.select(surviving_nodes),
            unclustered_members={member for member in self.unclustered_members if member in surviving_nodes},
        )

    def visit_paths(self, fn: Callable[[str], str]) -> None:
        self.partition.visit_paths(fn)


@dataclass
class ClusterCache:
    """A language's authoritative structural and unclustered lineage by scope."""

    scopes: dict[str, ClusterScopeLineage] = field(default_factory=dict)

    def detached_copy(self) -> ClusterCache:
        """Return an independent cache carrying the same partition and lineage."""
        return copy.deepcopy(self)

    def record_scope(
        self,
        partition: ClusterResult,
        unclustered_members: Collection[str] = (),
        scope_id: str = "",
    ) -> None:
        """Replace one scope's complete structural and unclustered lineage."""
        self.scopes[scope_id] = ClusterScopeLineage(partition, set(unclustered_members))

    def get_partition(self, scope_id: str = "") -> ClusterResult:
        lineage = self.scopes.get(scope_id)
        return lineage.partition if lineage is not None else ClusterResult()

    def get_unclustered_members(self, scope_id: str = "") -> set[str]:
        lineage = self.scopes.get(scope_id)
        return set(lineage.unclustered_members) if lineage is not None else set()

    def reroot_scope(self, child_id: str, parent_id: str) -> None:
        """Replace a parent subtree with its only child's complete lineage."""
        moving = {
            scope_id: lineage for scope_id, lineage in self.scopes.items() if self._scope_belongs_to(scope_id, child_id)
        }
        if not moving:
            if any(self._scope_belongs_to(scope_id, parent_id) for scope_id in self.scopes):
                raise ValueError(f"Cannot reroot clustering scope {child_id!r}; no lineage exists")
            return
        rerooted = {
            scope_id: lineage
            for scope_id, lineage in self.scopes.items()
            if not self._scope_belongs_to(scope_id, parent_id)
        }
        for scope_id, lineage in moving.items():
            moved = f"{parent_id}{scope_id[len(child_id) :]}" if parent_id else scope_id[len(child_id) :]
            target = moved.lstrip(".")
            if target in rerooted:
                raise ValueError(f"Cannot reroot clustering scope {scope_id!r}; target {target!r} already exists")
            rerooted[target] = lineage
        self.scopes = rerooted

    def select(self, surviving_nodes: Mapping[str, Node]) -> ClusterCache:
        """Return a copy keeping only ``surviving_nodes``, for filter/union of the graph."""
        return ClusterCache(
            scopes={scope_id: lineage.select(surviving_nodes) for scope_id, lineage in self.scopes.items()}
        )

    def visit_paths(self, fn: Callable[[str], str]) -> None:
        for lineage in self.scopes.values():
            lineage.visit_paths(fn)

    @staticmethod
    def _scope_belongs_to(scope_id: str, root: str) -> bool:
        return not root or scope_id == root or scope_id.startswith(f"{root}.")
