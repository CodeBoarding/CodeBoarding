"""Derive one scope's incremental update from the clustering, without asking an LLM.

Full analysis already fixes component count and membership deterministically
(``GroupingService.group`` + ``assemble_one_component_per_group``), leaving the LLM only
the naming. ``plan_scope_update`` gives the incremental path the same treatment: every
surviving component keeps what it owned, genuinely new clusters are absorbed, and only a
component left holding nothing is deleted. Structure is derived; wording stays the LLM's.

The anchor is the previous run's *methods*, not its cluster ids — see
``previous_ownership``.
"""

import logging
from collections import Counter
from pathlib import Path

import networkx as nx

from agents.agent_responses import (
    AnalysisInsights,
    ScopeOperation,
    ScopeOperationAction,
    ScopedClusterRef,
    ScopeUpdateDecision,
)
from agents.cluster_ids import CodeBoardingClusterIds
from agents.scope_ids import ROOT_SCOPE_ID
from diagram_analysis.exceptions import IncrementalClusteringError
from repo_utils.path_utils import normalize_repo_path
from static_analyzer.cluster_helpers import (
    combine_cluster_results,
    group_symbols,
)
from static_analyzer.clustering import ClusterResult
from static_analyzer.clustering.grouping import GroupingService

logger = logging.getLogger(__name__)


def previous_ownership(
    scope: AnalysisInsights, cluster_results: dict[str, ClusterResult], scope_id: str, repo_dir: Path
) -> dict[int, str]:
    """Leaf cluster id -> the component that previously owned most of its methods.

    Anchoring on methods rather than on the stored ``source_cluster_ids``: a scope's
    leaf clusters are re-derived from its subgraph on every run, so their integer ids
    renumber whenever the code inside the scope changes — exactly when anchoring
    matters. Qualified names survive that; they only disappear when the method does.

    Attributed one language at a time. A qualified name drops its file suffix
    (``src/index.py`` and ``src/index.ts`` both yield ``src.index.run``), so a single
    map keyed by bare qname would let one language's component claim the other's cluster.
    A file belongs to exactly one language's clusters, so restricting each language's
    owner map to its own files keeps the two apart. Cluster ids are already disjoint
    across languages, so the per-language results merge without collision.

    Both sides are normalized to repo-relative posix before the file match: ``cluster_to_files``
    carries the CFG's absolute paths (the static-analysis cache expands them under the repo)
    while ``file_methods`` is persisted relative, so a raw comparison would never match and
    strand method anchoring on the ``source_cluster_ids`` fallback — the very cluster-id
    anchoring this function exists to replace.

    A data-only component can be cluster-backed without holding methods. Unclaimed
    clusters therefore fall back to the owner recorded in ``source_cluster_ids``.
    """
    prefix = CodeBoardingClusterIds.prefix_for_scope(scope_id)
    claimed_ids: dict[str, str] = {
        cluster_id: component.component_id
        for component in scope.components
        if component.component_id
        for cluster_id in component.source_cluster_ids
    }
    owner: dict[int, str] = {}
    for cluster_result in cluster_results.values():
        language_files = {
            normalize_repo_path(path, repo_dir) for files in cluster_result.cluster_to_files.values() for path in files
        }
        owner_of_method: dict[str, str] = {
            method.qualified_name: component.component_id
            for component in scope.components
            if component.component_id
            for group in component.file_methods
            # A language without a file index (cluster_to_files empty) falls back to every
            # method, preserving single-language behaviour; the split only matters when two
            # languages carry colliding qnames, and then both have file indexes.
            if not language_files or normalize_repo_path(group.file_path, repo_dir) in language_files
            for method in group.methods
        }
        for cluster_id, members in cluster_result.clusters.items():
            tally = Counter(owner_of_method[member] for member in members if member in owner_of_method)
            if tally:
                # Ties go to the lowest component id, so the mapping is run-independent.
                owner[cluster_id] = min(tally.items(), key=lambda claim: (-claim[1], claim[0]))[0]
                continue
            qualified = CodeBoardingClusterIds.qualify_local_id(
                CodeBoardingClusterIds.from_graph_id(cluster_id), prefix
            )
            if qualified in claimed_ids:
                owner[cluster_id] = claimed_ids[qualified]
    return owner


def plan_scope_update(
    scope_id: str,
    scope: AnalysisInsights,
    cluster_results: dict[str, ClusterResult],
    cfg_graphs: dict[str, nx.DiGraph],
    changed_members: set[str],
    repo_dir: Path,
) -> ScopeUpdateDecision:
    """The operations that carry this scope's components onto the new clustering.

    ``UPDATE_COMPONENT`` for a component whose cluster set moved or whose code was edited;
    ``CREATE_COMPONENT`` only for a group with no predecessor; ``DELETE_COMPONENT`` only
    for a component left with nothing. Names and descriptions are deliberately absent —
    ``update_scope`` leaves the existing wording alone.

    A component that comes out of the grouping holding exactly what it already held gets
    **no operation at all**. An operation is not free: ``update_scope`` puts its target in
    ``refresh_ids``, which reruns the LLM relation analysis for the whole scope, and
    ``_remove_reassigned_clusters`` strips and restores the referenced clusters. Emitting
    one per survivor therefore relabels every relation in the tree on a one-line diff.
    """
    combined = combine_cluster_results(cluster_results)
    if not combined.clusters:
        # Every cluster is gone. Empty components are deleted; populated ones mean the
        # clustering failed to represent live code and must fail loudly.
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
    is_root = scope_id == ROOT_SCOPE_ID
    previous = previous_ownership(scope, cluster_results, scope_id, repo_dir)
    grouping = GroupingService().anchored_group(
        cluster_results,
        cfg_graphs,
        previous,
        subcomponents=not is_root,
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
    for group, owner in zip(grouping.groups, grouping.owners, strict=True):
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
            group_methods = {qname for cluster_id in group for qname in combined.clusters.get(cluster_id, ())}
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
                    name=_provisional_name(group, combined),
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
        f"[ScopePlan] {scope_id}: {len(grouping.groups)} groups, {untouched} unchanged (no operation), "
        f"{len(operations)} operation(s)" + (" (structure re-derived: drift past budget)" if grouping.regrouped else "")
    )
    return ScopeUpdateDecision(operations=operations)


def _provisional_name(group: set[int], combined: ClusterResult) -> str:
    """A stable placeholder for a component the LLM has not named yet."""
    symbols = group_symbols(sorted(group), combined.clusters)
    return symbols[0].split(".")[-1] if symbols else "New Component"


def _provisional_description(group: set[int], combined: ClusterResult) -> str:
    """Say what the component holds, so a created component never ships blank.

    Only the create path sets a new component's metadata — the re-detail pass that follows
    analyses its children, not its own wording — so an empty string here reaches the saved
    diagram. Naming it properly is the LLM's job and still to come; until then this states
    the code it owns rather than nothing.
    """
    files = sorted({path for cluster_id in group for path in combined.cluster_to_files.get(cluster_id, set())})
    symbols = group_symbols(sorted(group), combined.clusters)
    if not files and not symbols:
        return "New component with no resolved source files."
    named = ", ".join(symbols[:3])
    shown = ", ".join(files[:3]) + (f" (+{len(files) - 3} more)" if len(files) > 3 else "")
    return f"New component covering {len(symbols)} symbol(s) in {shown}. Entry points include {named}."
