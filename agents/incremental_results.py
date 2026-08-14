from dataclasses import dataclass, field

from static_analyzer.cfg import CallGraph
from static_analyzer.clustering import ClusterResult


@dataclass(frozen=True)
class ScopeRelationContext:
    """Static-analysis context required to regenerate one scope's relations."""

    cluster_results: dict[str, ClusterResult]
    cfg_graphs: dict[str, CallGraph]
    #: Components in this scope that actually moved. Relations between two components
    #: absent from this set are carried over verbatim instead of being re-worded.
    changed_ids: frozenset[str] = frozenset()


@dataclass
class ScopeUpdateResult:
    """Result of applying one incremental plan to one analysis scope."""

    relation_context: ScopeRelationContext
    refresh_ids: set[str] = field(default_factory=set)
    new_component_ids: set[str] = field(default_factory=set)
    removed_ids: set[str] = field(default_factory=set)


@dataclass
class RecursiveScopeUpdateResult:
    """Aggregated result from recursively updating expanded scopes."""

    refresh_ids: set[str] = field(default_factory=set)
    new_component_ids: set[str] = field(default_factory=set)
    removed_ids: set[str] = field(default_factory=set)
    relation_contexts: dict[str, ScopeRelationContext] = field(default_factory=dict)
