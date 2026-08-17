"""Collapse component scopes that contain exactly one child."""

import logging
from collections.abc import Sequence

from agents.agent_responses import AnalysisInsights, Relation
from static_analyzer.clustering import ClusterCache

logger = logging.getLogger(__name__)

ROOT_SCOPE = ""


def absorb_single_child_components(
    root_analysis: AnalysisInsights,
    sub_analyses: dict[str, AnalysisInsights],
    cluster_caches: Sequence[ClusterCache] = (),
) -> list[str]:
    """Collapse all single-child scopes to a fixpoint."""
    absorbed: list[str] = []
    while scopes := single_child_scopes(root_analysis, sub_analyses):
        absorbed.append(_absorb(scopes[0], root_analysis, sub_analyses, cluster_caches))
    return absorbed


def single_child_scopes(
    root_analysis: AnalysisInsights,
    sub_analyses: dict[str, AnalysisInsights],
) -> list[str]:
    """Scope ids that hold exactly one component — the violations of the invariant."""
    scopes = [ROOT_SCOPE] if _absorbable_at_root(root_analysis, sub_analyses) else []
    scopes.extend(sorted(scope_id for scope_id, scope in sub_analyses.items() if len(scope.components) == 1))
    return scopes


def _absorbable_at_root(root_analysis: AnalysisInsights, sub_analyses: dict[str, AnalysisInsights]) -> bool:
    """Return whether root absorption would leave at least one component."""
    if len(root_analysis.components) != 1:
        return False
    only_child = sub_analyses.get(root_analysis.components[0].component_id)
    return bool(only_child and only_child.components)


def _absorb(
    parent_id: str,
    root_analysis: AnalysisInsights,
    sub_analyses: dict[str, AnalysisInsights],
    cluster_caches: Sequence[ClusterCache] = (),
) -> str:
    scope = root_analysis if parent_id == ROOT_SCOPE else sub_analyses[parent_id]
    child = scope.components[0]
    child_id = child.component_id
    grandchildren = sub_analyses.get(child_id)

    if grandchildren is None or not grandchildren.components:
        del sub_analyses[parent_id]
    else:
        scope.components = grandchildren.components
        if parent_id != ROOT_SCOPE:
            scope.components_relations = grandchildren.components_relations
    sub_analyses.pop(child_id, None)
    _reroot_tree(root_analysis, sub_analyses, child_id, parent_id)
    for cache in cluster_caches:
        cache.method_paths.reroot_scope(child_id, parent_id)
    logger.info(f"[TreeShape] Absorbed '{child.name}' ({child_id}) into {parent_id or 'the root'}")
    return child_id


def _reroot_id(identifier: str, child_id: str, parent_id: str) -> str:
    """Move one dotted identifier out from under the absorbed child onto the parent."""
    prefix = f"{child_id}."
    if not identifier.startswith(prefix):
        return identifier
    tail = identifier[len(prefix) :]
    return f"{parent_id}.{tail}" if parent_id else tail


def _reroot_tree(
    root_analysis: AnalysisInsights,
    sub_analyses: dict[str, AnalysisInsights],
    child_id: str,
    parent_id: str,
) -> None:
    # Remove before reinserting because a shallower target may still be waiting to move.
    prefix = f"{child_id}."
    moving = {key: sub_analyses.pop(key) for key in [k for k in sub_analyses if k.startswith(prefix)]}
    for scope_id, scope in moving.items():
        sub_analyses[_reroot_id(scope_id, child_id, parent_id)] = scope
    for scope in [root_analysis, *sub_analyses.values()]:
        for component in scope.components:
            component.component_id = _reroot_id(component.component_id, child_id, parent_id)
            component.source_cluster_ids = [
                _reroot_id(cluster_id, child_id, parent_id) for cluster_id in component.source_cluster_ids
            ]
    live_names = {
        component.component_id: component.name
        for scope in [root_analysis, *sub_analyses.values()]
        for component in scope.components
    }
    for scope in [root_analysis, *sub_analyses.values()]:
        scope.components_relations = _reroot_relations(scope.components_relations, child_id, parent_id, live_names)


def _reroot_relations(
    relations: list[Relation], child_id: str, parent_id: str, live_names: dict[str, str]
) -> list[Relation]:
    """Re-point relation endpoints and drop collapsed self-relations."""
    survivors: list[Relation] = []
    for relation in relations:
        src_id = parent_id if relation.src_id == child_id else _reroot_id(relation.src_id, child_id, parent_id)
        dst_id = parent_id if relation.dst_id == child_id else _reroot_id(relation.dst_id, child_id, parent_id)
        if not src_id or not dst_id or src_id == dst_id:
            logger.debug(
                f"[TreeShape] Dropping relation {relation.src_id} -> {relation.dst_id} collapsed by absorption"
            )
            continue
        relation.src_id = src_id
        relation.dst_id = dst_id
        relation.src_name = live_names.get(src_id, relation.src_name)
        relation.dst_name = live_names.get(dst_id, relation.dst_name)
        survivors.append(relation)
    return survivors
