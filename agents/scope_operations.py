"""Share contracts and normalization for incremental scope operations."""

from agents.agent_responses import ScopeOperationAction, ScopedClusterRef
from agents.scope_ids import ROOT_SCOPE_ID
from diagram_analysis.cluster_delta import ClusterRef
from static_analyzer.graph import ClusterResult


EXISTING_COMPONENT_ACTIONS: frozenset[ScopeOperationAction] = frozenset(
    {
        ScopeOperationAction.UPDATE_COMPONENT,
        ScopeOperationAction.DELETE_COMPONENT,
        ScopeOperationAction.NOOP,
    }
)


def cluster_ref_from_scoped_ref(ref: ScopedClusterRef) -> ClusterRef:
    """Convert an LLM cluster reference to its canonical structural form."""
    return ClusterRef(ref.language, ref.cluster_id, ref.scope_id or ROOT_SCOPE_ID)


def cluster_member_qnames(cluster_results: dict[str, ClusterResult]) -> set[str]:
    """Return every qualified name represented in a scope's clusters."""
    return {
        qualified_name
        for cluster_result in cluster_results.values()
        for members in cluster_result.clusters.values()
        for qualified_name in members
    }


def normalize_component_name(name: str) -> str:
    """Normalize a component name for deterministic routing."""
    return " ".join(name.casefold().split())
