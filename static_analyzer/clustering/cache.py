"""Per-language clustering state, owned by ``LanguageResults``.

Kept off ``CallGraph`` so the graph stays pure structure: a partition is a
*result* about a graph, not part of it, and the same graph can be re-clustered
at a different scope without disturbing what an earlier run recorded.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field

from static_analyzer.clustering.method_cluster_paths import MethodClusterPaths
from static_analyzer.clustering.models import ClusterResult
from static_analyzer.node import Node


@dataclass
class ClusterCache:
    """The partition of a language's call graph, plus each method's scoped cluster path."""

    result: ClusterResult = field(default_factory=ClusterResult)
    method_paths: MethodClusterPaths = field(default_factory=MethodClusterPaths)

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

    def prune(self, surviving_nodes: Mapping[str, Node]) -> ClusterCache:
        """Return a copy restricted to ``surviving_nodes``, for filter/union of the graph."""
        return ClusterCache(
            result=self.result.prune(surviving_nodes),
            method_paths=self.method_paths.prune(surviving_nodes),
        )

    def visit_paths(self, fn: Callable[[str], str]) -> None:
        self.result.visit_paths(fn)
