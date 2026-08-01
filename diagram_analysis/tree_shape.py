"""Whole-tree shape repair: no parent may hold exactly one child.

A component whose expansion produced a single sub-component adds a level that
explains nothing — parent and child describe the same code, one box inside
another. Absorbing the child hands its own children to the parent and removes
one level from that branch; a childless child just disappears and the parent
becomes a leaf.

The repair is a prefix re-rooting. Every identifier in this tree — component id,
``sub_analyses`` key, scoped cluster id, relation endpoint — is the dotted path
to a position, so moving the absorbed child's subtree onto the parent's path is
one rewrite of ``"<child_id>."`` to ``"<parent_id>."`` applied everywhere. That
also renumbers the promoted siblings for free, and cannot collide: a parent with
exactly one child has no other child to collide with.

What is deliberately NOT rewritten is the cluster lineage pickled with the CFG
(``MethodClusterPaths``). It looks like the same rename and is not: lineage is
recorded per scope, so the absorbed child's leaf clusters and the parent's own
are two independent clusterings that both land on ``<parent_id>.<n>``, and
merging them invents a cluster containing the union of two unrelated ones. The
parent's recorded lineage is still correct after an absorption — its membership
never changed — so leaving it alone is both simpler and the only right answer. A
promoted scope whose id moved gets a stale seed, which costs a biased warm start
and nothing more: ``filter_by_nodes`` prunes the lineage to the component's own
methods before it is read, and the seed is an initial partition Leiden then
re-optimises.
"""

import logging

from agents.agent_responses import AnalysisInsights, Relation

logger = logging.getLogger(__name__)

#: ``sub_analyses`` is keyed by component id and the root analysis has no key.
#: Empty string, not ``ROOT_SCOPE_ID``: this is the *prefix* sense of the root,
#: the one ``agents.cluster_ids.prefix_for_scope`` returns for it.
ROOT_SCOPE = ""


def drop_dangling_key_entities(
    root_analysis: AnalysisInsights,
    sub_analyses: dict[str, AnalysisInsights],
) -> list[str]:
    """Drop key entities naming a symbol no component owns; return what went.

    A key entity is a pointer into the code, so a deleted symbol must take its pointers
    with it. Membership already works that way — several passes strip a method the moment
    it leaves — but key entities are scrubbed by one pass, ``fix_key_entities_refs``, which
    is gated on the scopes an update touched. A component emptied by a route that did not
    touch it keeps entities pointing at symbols that no longer exist, and because
    ``prune_empty_components`` reads a non-empty ``key_entities`` as a component still
    having something to describe, the empty box then survives the prune.

    Measured over the baselines in CodeBoarding-evals and the corpus: 557 of 557 key
    entities in healthy documents name a method some component owns, so enforcing that
    cannot remove a legitimate reference. Whole-tree and unconditional, because "the symbol
    is gone" is true of the document rather than of whichever scope an update happened to
    visit.
    """
    owned = {
        method.qualified_name
        for scope in [root_analysis, *sub_analyses.values()]
        for component in scope.components
        for group in component.file_methods
        for method in group.methods
    }
    dropped: list[str] = []
    for scope in [root_analysis, *sub_analyses.values()]:
        for component in scope.components:
            surviving = [entity for entity in component.key_entities if entity.qualified_name in owned]
            if len(surviving) != len(component.key_entities):
                dropped.extend(
                    entity.qualified_name for entity in component.key_entities if entity.qualified_name not in owned
                )
                component.key_entities = surviving
    if dropped:
        logger.info(f"[TreeShape] Dropped {len(dropped)} key entity/entities naming symbols no component owns")
    return dropped


def absorb_single_child_components(
    root_analysis: AnalysisInsights,
    sub_analyses: dict[str, AnalysisInsights],
) -> list[str]:
    """Collapse every scope holding exactly one component; return the ids absorbed.

    Runs to a fixpoint, so a degenerate ``1 -> 1.1 -> 1.1.1`` chain comes out as a
    single component. Idempotent: a tree with no single-child scope is untouched.
    """
    absorbed: list[str] = []
    while scopes := single_child_scopes(root_analysis, sub_analyses):
        absorbed.append(_absorb(scopes[0], root_analysis, sub_analyses))
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
    """Whether the root's lone component can be absorbed without emptying the analysis.

    Why: the root is a scope but not a component, so there is nothing for a childless
    lone component to be absorbed *into* — collapsing it would leave no components at all.
    """
    if len(root_analysis.components) != 1:
        return False
    only_child = sub_analyses.get(root_analysis.components[0].component_id)
    return bool(only_child and only_child.components)


def _absorb(
    parent_id: str,
    root_analysis: AnalysisInsights,
    sub_analyses: dict[str, AnalysisInsights],
) -> str:
    scope = root_analysis if parent_id == ROOT_SCOPE else sub_analyses[parent_id]
    child = scope.components[0]
    child_id = child.component_id
    grandchildren = sub_analyses.get(child_id)

    if grandchildren is None or not grandchildren.components:
        # The parent already owns every method the child did, so dropping the child
        # loses nothing and the parent becomes a leaf.
        del sub_analyses[parent_id]
    else:
        scope.components = grandchildren.components
        if parent_id != ROOT_SCOPE:
            # The parent's own relations related its single child to nothing. The child's
            # relate the components now standing in its place. The root's list is the
            # global cross-boundary set, not a sibling set, so it is left alone.
            scope.components_relations = grandchildren.components_relations
    sub_analyses.pop(child_id, None)
    _reroot_tree(root_analysis, sub_analyses, child_id, parent_id)
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
    for scope_id in [key for key in sub_analyses if key.startswith(f"{child_id}.")]:
        sub_analyses[_reroot_id(scope_id, child_id, parent_id)] = sub_analyses.pop(scope_id)
    for scope in [root_analysis, *sub_analyses.values()]:
        for component in scope.components:
            component.component_id = _reroot_id(component.component_id, child_id, parent_id)
            component.source_cluster_ids = [
                _reroot_id(cluster_id, child_id, parent_id) for cluster_id in component.source_cluster_ids
            ]
        scope.components_relations = _reroot_relations(scope.components_relations, child_id, parent_id)


def _reroot_relations(relations: list[Relation], child_id: str, parent_id: str) -> list[Relation]:
    """Re-point relation endpoints, dropping the ones the collapse makes meaningless.

    An endpoint naming the absorbed child itself becomes the parent, which can turn a
    cross-component edge into a self-loop; at the root there is no parent to fall back
    on. Both are dropped rather than rendered as an edge from a box to itself.
    """
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
        survivors.append(relation)
    return survivors
