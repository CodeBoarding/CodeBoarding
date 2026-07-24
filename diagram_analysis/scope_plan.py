"""Derive one scope's incremental update from the clustering, without asking an LLM.

Full analysis already fixes component count and membership deterministically
(``supercluster_leaf_ids`` + ``assemble_one_component_per_group``), leaving the LLM only
the naming. ``plan_scope_update`` gives the incremental path the same treatment: every
surviving component keeps what it owned, genuinely new clusters are absorbed, and only a
component left holding nothing is deleted. Structure is derived; wording stays the LLM's.

The anchor is the previous run's *methods*, not its cluster ids — see
``previous_ownership``.
"""

import logging
from collections import Counter

import networkx as nx

from agents.agent_responses import (
    AnalysisInsights,
    ScopeOperation,
    ScopeOperationAction,
    ScopedClusterRef,
    ScopeUpdateDecision,
)
from agents.scope_ids import ROOT_SCOPE_ID
from static_analyzer.cluster_helpers import (
    SUBCOMPONENTS_MAX,
    SUBCOMPONENTS_MIN,
    TOP_LEVEL_COMPONENTS_MAX,
    TOP_LEVEL_COMPONENTS_MIN,
    anchored_grouping,
    combine_cluster_results,
    group_symbols,
)
from static_analyzer.graph import ClusterResult

logger = logging.getLogger(__name__)


def previous_ownership(scope: AnalysisInsights, cluster_result: ClusterResult) -> dict[int, str]:
    """Leaf cluster id -> the component that previously owned most of its methods.

    Anchoring on methods rather than on the stored ``source_cluster_ids``: a scope's
    leaf clusters are re-derived from its subgraph on every run, so their integer ids
    renumber whenever the code inside the scope changes — exactly when anchoring
    matters. Qualified names survive that; they only disappear when the method does.
    """
    owner_of_method: dict[str, str] = {
        method.qualified_name: component.component_id
        for component in scope.components
        if component.component_id
        for group in component.file_methods
        for method in group.methods
    }
    owner: dict[int, str] = {}
    for cluster_id, members in cluster_result.clusters.items():
        tally = Counter(owner_of_method[member] for member in members if member in owner_of_method)
        if tally:
            # Ties go to the lowest component id, so the mapping is run-independent.
            owner[cluster_id] = min(tally.items(), key=lambda claim: (-claim[1], claim[0]))[0]
    return owner


def plan_scope_update(
    scope_id: str,
    scope: AnalysisInsights,
    cluster_results: dict[str, ClusterResult],
    cfg_graphs: dict[str, nx.DiGraph],
) -> ScopeUpdateDecision:
    """The operations that carry this scope's components onto the new clustering.

    ``UPDATE_COMPONENT`` for every component that survives, carrying the clusters it now
    owns; ``CREATE_COMPONENT`` only for a group with no predecessor; ``DELETE_COMPONENT``
    only for a component left with nothing. Names and descriptions are deliberately
    absent — ``update_scope`` leaves the existing wording alone, and refreshing it is the
    LLM's job downstream.
    """
    combined = combine_cluster_results(cluster_results)
    if not combined.clusters:
        return ScopeUpdateDecision(operations=[])
    combined_cfg: nx.DiGraph = nx.compose_all(list(cfg_graphs.values())) if cfg_graphs else nx.DiGraph()

    is_root = scope_id == ROOT_SCOPE_ID
    low = TOP_LEVEL_COMPONENTS_MIN if is_root else SUBCOMPONENTS_MIN
    high = TOP_LEVEL_COMPONENTS_MAX if is_root else SUBCOMPONENTS_MAX

    previous = previous_ownership(scope, combined)
    grouping = anchored_grouping(combined, combined_cfg, previous, low, high)

    language_of: dict[int, str] = {
        cluster_id: language for language, result in cluster_results.items() for cluster_id in result.clusters
    }

    operations: list[ScopeOperation] = []
    kept: set[str] = set()
    for group, owner in zip(grouping.groups, grouping.owners):
        refs = [
            ScopedClusterRef(scope_id=scope_id, language=language_of.get(cluster_id, ""), cluster_id=cluster_id)
            for cluster_id in sorted(group)
        ]
        if owner:
            kept.add(owner)
            operations.append(
                ScopeOperation(
                    action=ScopeOperationAction.UPDATE_COMPONENT,
                    component_id=owner,
                    cluster_refs=refs,
                    rationale="carried forward from the previous grouping",
                )
            )
        else:
            operations.append(
                ScopeOperation(
                    action=ScopeOperationAction.CREATE_COMPONENT,
                    cluster_refs=refs,
                    name=_provisional_name(group, combined),
                    description="",
                    rationale="clusters with no predecessor in this scope",
                )
            )

    for component in scope.components:
        if component.component_id and component.component_id not in kept:
            operations.append(
                ScopeOperation(
                    action=ScopeOperationAction.DELETE_COMPONENT,
                    component_id=component.component_id,
                    cluster_refs=[],
                    rationale="holds no cluster in the new grouping",
                )
            )

    logger.info(
        f"[ScopePlan] {scope_id}: {len(grouping.groups)} groups, {len(kept)} carried, "
        f"{len(operations) - len(kept)} created/deleted"
        + (" (structure re-derived: drift past budget)" if grouping.regrouped else "")
    )
    return ScopeUpdateDecision(operations=operations)


def _provisional_name(group: set[int], combined: ClusterResult) -> str:
    """A stable placeholder for a component the LLM has not named yet."""
    symbols = group_symbols(sorted(group), combined.clusters)
    return symbols[0].split(".")[-1] if symbols else "New Component"
