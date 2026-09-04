"""Translate deterministic clustering scopes into incremental component operations.

Every surviving component keeps what it owned, genuinely new groups are created under the
id the tree specification gave them, and only a component left holding nothing is deleted.
"""

import logging

from agents.agent_responses import (
    AnalysisInsights,
    ScopeOperation,
    ScopeOperationAction,
    ScopedClusterRef,
    ScopeUpdateDecision,
)
from diagram_analysis.exceptions import IncrementalClusteringError
from clustering_ids import CodeBoardingClusterIds
from static_analyzer.clustering import ClusterGroup, ClusterResult, ClusterScopeResult

logger = logging.getLogger(__name__)


def plan_scope_result_update(
    scope: AnalysisInsights,
    clustering: ClusterScopeResult,
    changed_members: set[str],
) -> ScopeUpdateDecision:
    """Carry a persisted scope onto one precomputed structural result."""
    return _plan_scope_operations(
        clustering.scope_id,
        scope,
        clustering.leaf_clusters_by_language,
        clustering.groups,
        changed_members,
    )


def _plan_scope_operations(
    scope_id: str,
    scope: AnalysisInsights,
    cluster_results: dict[str, ClusterResult],
    groups: list[ClusterGroup],
    changed_members: set[str],
) -> ScopeUpdateDecision:
    combined = _combine_cluster_results(cluster_results)
    if not combined.clusters:
        still_populated = [
            component.component_id
            for component in scope.components
            if component.component_id and any(group.methods for group in component.file_methods)
        ]
        if still_populated:
            raise IncrementalClusteringError(scope_id, still_populated)
        return ScopeUpdateDecision(
            operations=[
                ScopeOperation(
                    action=ScopeOperationAction.DELETE_COMPONENT,
                    component_id=component.component_id,
                    cluster_refs=[],
                    rationale="every cluster in this scope is gone",
                )
                for component in scope.components
                if component.component_id
            ]
        )
    language_of: dict[int, str] = {
        cluster_id: language for language, result in cluster_results.items() for cluster_id in result.clusters
    }

    prefix = CodeBoardingClusterIds.prefix_for_scope(scope_id)
    held_clusters: dict[str, set[str]] = {}
    held_methods: dict[str, set[str]] = {}
    edited: set[str] = set()
    for component in scope.components:
        if not component.component_id:
            continue
        methods = {method.qualified_name for group in component.file_methods for method in group.methods}
        held_clusters[component.component_id] = set(component.source_cluster_ids)
        held_methods[component.component_id] = methods
        if methods & changed_members:
            edited.add(component.component_id)

    operations: list[ScopeOperation] = []
    kept: set[str] = set()
    untouched = 0
    surviving = {group.previous_component_id for group in groups if group.previous_component_id}
    sibling_names = {component.name for component in scope.components if component.component_id in surviving}
    for cluster_group in groups:
        group = set(cluster_group.cluster_ids)
        owner = cluster_group.previous_component_id
        refs = [
            ScopedClusterRef(scope_id=scope_id, language=language_of.get(cluster_id, ""), cluster_id=cluster_id)
            for cluster_id in sorted(group)
        ]
        if owner:
            kept.add(owner)
            qualified = set(
                CodeBoardingClusterIds.qualify_local_ids(CodeBoardingClusterIds.from_graph_ids(group), prefix)
            )
            # Both the cluster ids and the methods behind them must be unchanged. Cluster
            # ids alone are not enough: a newly added method is absorbed into an existing
            # cluster, leaving the id set identical, and it is absent from the component's
            # pre-update file_methods so ``edited`` cannot see it either. Skipping then
            # would drop the addition from the analysis entirely.
            group_methods = cluster_group.qualified_names
            if (
                qualified == held_clusters.get(owner)
                and group_methods == held_methods.get(owner)
                and owner not in edited
            ):
                untouched += 1
                continue
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
                    # The specification allocated this id; the component must keep it or the
                    # next replay would not recognise its own rule.
                    component_id=cluster_group.group_id,
                    name=cluster_group.unique_name(sibling_names),
                    description=_provisional_description(group, combined),
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
        f"[ScopePlan] {scope_id}: {len(groups)} groups, {untouched} unchanged (no operation), "
        f"{len(operations)} operation(s)"
    )
    for operation in operations:
        logger.info(f"[ScopePlan] {scope_id}: {operation.action.value} {operation.component_id}: {operation.rationale}")
    return ScopeUpdateDecision(operations=operations)


def _combine_cluster_results(cluster_results: dict[str, ClusterResult]) -> ClusterResult:
    """Union per-language leaf clusters; their ids are disjoint across languages."""
    combined = ClusterResult(strategy="combined")
    for result in cluster_results.values():
        combined.clusters.update(result.clusters)
        combined.cluster_to_files.update(result.cluster_to_files)
        for file_path, cluster_ids in result.file_to_clusters.items():
            combined.file_to_clusters.setdefault(file_path, set()).update(cluster_ids)
    return combined


def _group_symbols(cluster_ids: list[int], node_lookup: dict[int, set[str]]) -> list[str]:
    """Qualified names in a group, most top-level first (fewest name segments)."""
    names = {qname for cid in cluster_ids for qname in node_lookup.get(cid, set())}
    return sorted(names, key=lambda qname: (qname.count("."), qname))


def _provisional_description(group: set[int], combined: ClusterResult) -> str:
    """Say what a newly created component holds."""
    files = sorted({path for cluster_id in group for path in combined.cluster_to_files.get(cluster_id, set())})
    symbols = _group_symbols(sorted(group), combined.clusters)
    if not files and not symbols:
        return "New component with no resolved source files."
    named = ", ".join(symbols[:3])
    shown = ", ".join(files[:3]) + (f" (+{len(files) - 3} more)" if len(files) > 3 else "")
    return f"New component covering {len(symbols)} symbol(s) in {shown}. Entry points include {named}."
