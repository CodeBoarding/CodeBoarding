"""
Static-analysis-based inter-component relationship building.

This module builds relationships between components from actual CFG (Call Flow Graph)
edges — no LLM needed.
"""

import logging
from collections import defaultdict
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field

from constants import DEFAULT_STATIC_RELATION_LABEL
from agents.agent_responses import AnalysisInsights, Relation, RelationEdge, SourceCodeReference
from agents.relation_edges import append_or_merge_relation, drop_internal_self_relations
from static_analyzer.graph import CallGraph

logger = logging.getLogger(__name__)


@dataclass
class ClusterRelation:
    """A relationship between two components derived from static CFG analysis."""

    src_cluster_id: str  # component's component_id, e.g. "1.2"
    dst_cluster_id: str  # e.g. "3"
    all_edges: list[RelationEdge] = field(default_factory=list)


def build_node_to_component_map(analysis: AnalysisInsights) -> dict[str, str]:
    """Map node qualified_name -> component.component_id using file_methods.

    Every node assigned to a component via populate_file_methods() is mapped
    to that component's hierarchical ID.
    """
    node_to_component: dict[str, str] = {}
    for comp in analysis.components:
        for fg in comp.file_methods:
            for method in fg.methods:
                node_to_component[method.qualified_name] = comp.component_id
    return node_to_component


def build_global_node_to_component_map(
    root_analysis: AnalysisInsights,
    sub_analyses: dict[str, AnalysisInsights],
) -> dict[str, str]:
    """Map each node to the deepest currently expanded component that owns it."""
    node_to_component = build_node_to_component_map(root_analysis)
    for parent_id, sub_analysis in sorted(sub_analyses.items(), key=lambda item: (item[0].count("."), item[0])):
        for node_name, component_id in build_node_to_component_map(sub_analysis).items():
            if component_id.startswith(f"{parent_id}."):
                node_to_component[node_name] = component_id
    return node_to_component


def _qnames_match(a: str, b: str) -> bool:
    """Whether two edge-endpoint qualified names denote the same symbol.

    The CFG names a symbol canonically (``src.pkg.mod.Cls.m``); the LLM often writes a
    non-canonical variant of the same symbol — a ``Cls:m`` class separator, or a module
    path missing the source-root prefix (``pkg.mod.Cls.m``). Normalise the separator and
    accept a suffix match so a highlight is tied to its real CFG edge rather than kept as a
    drifting duplicate.
    """
    a, b = a.replace(":", "."), b.replace(":", ".")
    return a == b or a.endswith("." + b) or b.endswith("." + a)


def build_owner_index(node_to_component: dict[str, str]) -> dict[str, list[tuple[str, str]]]:
    """Group the component map by each symbol's last segment.

    Why: ``_qnames_match`` can only match names whose final segment agrees, so the leaf narrows
    the candidates from every symbol in the repo to a handful — the difference between a full
    scan per endpoint and a lookup.
    """
    index: dict[str, list[tuple[str, str]]] = {}
    for qname, component_id in node_to_component.items():
        index.setdefault(qname.rsplit(".", 1)[-1], []).append((qname, component_id))
    return index


def _endpoint_owner(reference: SourceCodeReference, owner_index: dict[str, list[tuple[str, str]]]) -> str:
    """Component owning this endpoint's symbol; empty when unknown or ambiguous."""
    qname = reference.qualified_name.replace(":", ".")
    owners = {
        component_id
        for candidate, component_id in owner_index.get(qname.rsplit(".", 1)[-1], ())
        if _qnames_match(qname, candidate)
    }
    return owners.pop() if len(owners) == 1 else ""


def edge_crosses_components(
    edge: RelationEdge,
    owner_index: dict[str, list[tuple[str, str]]],
    src_id: str,
    dst_id: str,
) -> bool:
    """Whether an LLM-authored edge actually runs from ``src_id`` to ``dst_id``.

    Why: a runtime/config pair has no static edge to ground against, so an edge whose endpoints
    both land inside one component is serialized as if it crossed the boundary, and the diagram
    draws that component's file under the other one. Ownership is satisfied by the declared
    component or any descendant, so a deliberate cross-depth edge still passes. Whether the
    symbol exists at all is not decided here — an endpoint no component owns may be external
    code, which ``keep_relation_edge`` judges against the symbol table.
    """
    for reference, component_id in ((edge.source, src_id), (edge.target, dst_id)):
        owner = _endpoint_owner(reference, owner_index)
        if owner and component_id and not is_self_or_descendant(owner, component_id):
            return False
    return True


def prune_ungrounded_edges(
    relations: list[Relation],
    owner_index: dict[str, list[tuple[str, str]]],
    keep_edge: Callable[[RelationEdge], bool],
) -> list[Relation]:
    """Re-apply the edge filters to an assembled relation list, moving edges rather than losing them.

    Why it runs again: the filters guard the edges a run AUTHORS, but preservation re-injects the
    baseline's edges afterwards — verbatim for a pair the rebuild did not touch, and per-edge
    wherever two methods came through unchanged. A baseline written by an older engine therefore
    keeps its mis-attributed edges for as long as nobody edits the methods they name, which is
    indefinitely.

    A mis-attributed edge is a REAL call filed under the wrong pair, so it is re-filed with the
    relation whose declared endpoints match where the call actually runs. Only two things remove
    an edge: an endpoint that names no live symbol (``keep_edge``), and a duplicate the correct
    pair already carries. Measured on one stored analysis, dropping instead of re-filing orphaned
    25 of 28 real calls — the mis-attribution is worth repairing, the call is not worth losing.

    An edge whose true pair has no relation to move to stays where it is: inventing a relation
    here would be authoring architecture in a filter, and silently deleting the call would be
    worse than leaving it mis-filed where a check can still flag it.
    """
    by_pair: dict[tuple[str, str], Relation] = {(relation.src_id, relation.dst_id): relation for relation in relations}
    additions: dict[tuple[str, str], list[RelationEdge]] = {}

    def rehome(edge: RelationEdge) -> tuple[str, str] | None | bool:
        """Where this edge belongs: a declared pair, ``None`` for nowhere, ``False`` for nothing.

        ``False`` means both endpoints sit in one component, so there is no cross-component fact
        to file anywhere — `build_component_relations` discards those at the source, and it is
        the shape the original report was about (`build_cta.main -> build_cta.build_cta` offered
        as evidence that one component reaches another).
        """
        source = _endpoint_owner(edge.source, owner_index)
        target = _endpoint_owner(edge.target, owner_index)
        if not source or not target:
            return None
        if is_self_or_descendant(source, target) or is_self_or_descendant(target, source):
            return False
        for pair in by_pair:
            if is_self_or_descendant(source, pair[0]) and is_self_or_descendant(target, pair[1]):
                return pair
        return None

    kept: list[Relation] = []
    for relation in relations:
        pair = (relation.src_id, relation.dst_id)
        all_edges: list[RelationEdge] = []
        for edge in relation.all_edges:
            if not keep_edge(edge):
                continue
            if edge_crosses_components(edge, owner_index, relation.src_id, relation.dst_id):
                all_edges.append(edge)
                continue
            home = rehome(edge)
            if home is False:
                continue  # Internal call: no cross-component fact exists to preserve.
            if home is None or home == pair:
                # Nowhere better to file it; keeping a mis-filed call beats deleting a real one.
                all_edges.append(edge)
            else:
                additions.setdefault(home, []).append(edge)  # type: ignore[arg-type]
        surviving = {edge.identity() for edge in all_edges}
        key_edges = [edge for edge in relation.key_edges if edge.identity() in surviving]
        kept.append(relation.model_copy(update={"all_edges": all_edges, "key_edges": key_edges}))

    settled: list[Relation] = []
    for relation in kept:
        moved = additions.get((relation.src_id, relation.dst_id))
        if moved:
            relation = relation.model_copy(update={"all_edges": Relation._unique_edges([*relation.all_edges, *moved])})
        if not relation.all_edges and not relation.key_edges and not relation.evidence.strip():
            continue
        settled.append(relation)

    return drop_reverse_duplicates(settled)


def drop_reverse_duplicates(relations: list[Relation]) -> list[Relation]:
    """Collapse a pair stated twice, once in each direction.

    A relation is backed by concrete method-to-method calls — the ``all_edges``/``key_edges``
    that show WHERE one component reaches the other. Two cases arise:

    *One side has those calls and the other does not.* The bare side is the same connection
    written backwards, surviving on prose: `3.3 -> 3.1` said so while `3.1 -> 3.3` held the
    actual `parse_unified_analysis -> _extract_analysis_recursive`. The grounded one wins.

    *Neither side has any.* Then nothing distinguishes them — the model simply narrated one
    connection twice, in both directions. Both are kept today, and which one survives the next
    run is luck: on `single-method-added` one engine's baseline carried both and its update
    dropped one (reported as a deleted edge) while the other's baseline carried one and its
    update added the second (reported as an added edge). The test forbids both. So one is
    chosen deterministically — lowest source id, then longest evidence — and it is the same
    choice every run.

    Applied on every path, full and incremental alike. Applying it to only one makes a baseline
    and the update disagree about what a clean document looks like, and the difference lands as
    phantom churn on a commit that changed nothing.
    """
    grounded_pairs = {
        (relation.src_id, relation.dst_id) for relation in relations if relation.all_edges or relation.key_edges
    }
    kept: list[Relation] = []
    dropped_bare: set[tuple[str, str]] = set()
    by_pair = {(relation.src_id, relation.dst_id): relation for relation in relations}
    for relation in relations:
        pair = (relation.src_id, relation.dst_id)
        reverse = (relation.dst_id, relation.src_id)
        if relation.all_edges or relation.key_edges or relation.is_static:
            kept.append(relation)
            continue
        if reverse in grounded_pairs:
            continue  # The grounded side already states this connection, the right way round.
        # `reverse == pair` is a self-relation, which `drop_internal_self_relations` owns.
        other = by_pair.get(reverse) if reverse != pair else None
        if other is not None and not (other.all_edges or other.key_edges or other.is_static):
            # Same claim twice with nothing to tell them apart. Pick once, deterministically:
            # the fuller explanation survives, and an exact tie falls to the lower source id.
            loser = max((relation, other), key=lambda r: (-len(r.evidence or ""), r.src_id, r.dst_id))
            if (loser.src_id, loser.dst_id) == pair and reverse not in dropped_bare:
                dropped_bare.add(pair)
                continue
        kept.append(relation)
    return kept


def ground_relation_edges(
    llm_key_edges: list[RelationEdge], static_edges: list[RelationEdge]
) -> tuple[list[RelationEdge], list[RelationEdge]]:
    """Ground a relation's edges in the deterministic static CFG.

    For a statically-backed pair the CFG edges are the complete cross-component call set, so
    they ARE ``all_edges`` — the LLM contributes wording, never edges. Its ``key_edges`` are
    kept only where they name a real CFG edge (as the canonical CFG edge), so an edge the LLM
    invented or spelled differently each run can no longer appear, vanish, or duplicate. A
    pair with no static edge is a runtime/config relation whose ``key_edges`` are its only
    evidence, so those are left as-is.

    Returns ``(key_edges, all_edges)``. ``key_edges`` is always a subset of ``all_edges`` by
    edge identity (which ignores the description), so a later re-merge stays idempotent.
    """
    static_unique = Relation._unique_edges(static_edges)
    if not static_unique:
        merged = Relation._unique_edges(llm_key_edges)
        return merged, merged
    highlighted: list[RelationEdge] = []
    for static_edge in static_unique:
        match = next(
            (
                key
                for key in llm_key_edges
                if _qnames_match(key.source.qualified_name, static_edge.source.qualified_name)
                and _qnames_match(key.target.qualified_name, static_edge.target.qualified_name)
            ),
            None,
        )
        if match is None:
            continue
        # Keep the canonical CFG edge (real spans and call sites), but carry over the LLM's
        # per-edge description so the reader's wording is not lost to the grounding.
        highlighted.append(
            static_edge.model_copy(update={"description": match.description}) if match.description else static_edge
        )
    return highlighted, static_unique


def build_component_relations(
    node_to_component: dict[str, str],
    cfg_graphs: dict[str, CallGraph],
) -> list[ClusterRelation]:
    """Build inter-component relations from actual CFG edges.

    For every CFG edge where src and dst belong to different components,
    count and collect the concrete bridge methods.

    Args:
        node_to_component: Mapping from node qualified_name to component_id.
        cfg_graphs: Mapping from language to CallGraph.

    Returns:
        List of ClusterRelation objects, one per (src_component, dst_component) pair.
    """
    edge_pairs: dict[tuple[str, str], list[RelationEdge]] = defaultdict(list)
    for cfg in cfg_graphs.values():
        for edge in cfg.edges:
            src_name = edge.get_source()
            dst_name = edge.get_destination()
            src_comp = node_to_component.get(src_name)
            dst_comp = node_to_component.get(dst_name)
            if src_comp and dst_comp and src_comp != dst_comp:
                key = (src_comp, dst_comp)
                edge_pairs[key].append(RelationEdge.from_edge(edge))

    relations = []
    for (src_c, dst_c), edges in sorted(edge_pairs.items()):
        relations.append(
            ClusterRelation(
                src_cluster_id=src_c,
                dst_cluster_id=dst_c,
                all_edges=edges,
            )
        )

    logger.info(f"Built {len(relations)} static inter-component relations from CFG edges")
    return relations


def iter_ancestor_ids(component_id: str) -> Iterator[str]:
    """Yield component_id then each shorter dotted-prefix ancestor."""
    parts = component_id.split(".")
    for i in range(len(parts), 0, -1):
        yield ".".join(parts[:i])


def is_self_or_descendant(component_id: str, ancestor_id: str) -> bool:
    """True when component_id is ancestor_id or one of its dotted descendants."""
    return component_id == ancestor_id or component_id.startswith(f"{ancestor_id}.")


def _collect_component_names(
    root_analysis: AnalysisInsights, sub_analyses: dict[str, AnalysisInsights]
) -> dict[str, str]:
    id_to_name = {comp.component_id: comp.name for comp in root_analysis.components}
    for sub_analysis in sub_analyses.values():
        id_to_name.update({comp.component_id: comp.name for comp in sub_analysis.components})
    return id_to_name


def _collect_authoritative_relations(
    root_analysis: AnalysisInsights, sub_analyses: dict[str, AnalysisInsights]
) -> list[Relation]:
    """Select one metadata source per component pair, preferring live scopes."""
    relations_by_pair: dict[tuple[str, str], Relation] = {}
    analyses = [
        root_analysis,
        *(sub_analyses[scope_id] for scope_id in sorted(sub_analyses, key=lambda item: (item.count("."), item))),
    ]
    for analysis in analyses:
        for relation in analysis.components_relations:
            if relation.src_id and relation.dst_id:
                relations_by_pair[(relation.src_id, relation.dst_id)] = relation
    return list(relations_by_pair.values())


def _ancestor_relation(src_id: str, dst_id: str, llm_relations: list[Relation]) -> Relation | None:
    candidates = [
        rel
        for rel in llm_relations
        if rel.src_id
        and rel.dst_id
        and is_self_or_descendant(src_id, rel.src_id)
        and is_self_or_descendant(dst_id, rel.dst_id)
    ]
    if not candidates:
        return None
    candidates.sort(key=lambda rel: (-(rel.src_id.count(".") + rel.dst_id.count(".")), rel.src_id, rel.dst_id))
    return candidates[0]


def _relation_key_edges_for_pair(
    relation: Relation,
    src_id: str,
    dst_id: str,
    node_to_component: dict[str, str],
) -> list[RelationEdge]:
    if (relation.src_id, relation.dst_id) == (src_id, dst_id):
        return relation.key_edges
    return [
        edge
        for edge in relation.key_edges
        if node_to_component.get(edge.source.qualified_name) == src_id
        and node_to_component.get(edge.target.qualified_name) == dst_id
    ]


def build_global_relations(
    root_analysis: AnalysisInsights,
    sub_analyses: dict[str, AnalysisInsights],
    cfg_graphs: dict[str, CallGraph],
) -> list[Relation]:
    """Build deterministic project-wide relations at the current expansion frontier."""
    node_to_component = build_global_node_to_component_map(root_analysis, sub_analyses)
    static_relations = build_component_relations(node_to_component, cfg_graphs)
    id_to_name = _collect_component_names(root_analysis, sub_analyses)
    live_ids = set(id_to_name)
    llm_relations = _collect_authoritative_relations(root_analysis, sub_analyses)

    global_relations: dict[tuple[str, str], Relation] = {}
    static_pairs = {(rel.src_cluster_id, rel.dst_cluster_id) for rel in static_relations}
    superseded_llm_pairs: set[tuple[str, str]] = set()

    for static_rel in static_relations:
        src_id = static_rel.src_cluster_id
        dst_id = static_rel.dst_cluster_id
        for llm_rel in llm_relations:
            if (
                llm_rel.src_id
                and llm_rel.dst_id
                and is_self_or_descendant(src_id, llm_rel.src_id)
                and is_self_or_descendant(dst_id, llm_rel.dst_id)
            ):
                superseded_llm_pairs.add((llm_rel.src_id, llm_rel.dst_id))
        llm_relation = _ancestor_relation(src_id, dst_id, llm_relations)
        if llm_relation is None:
            relation = Relation.from_edges(
                DEFAULT_STATIC_RELATION_LABEL,
                id_to_name.get(src_id, src_id),
                id_to_name.get(dst_id, dst_id),
                src_id,
                dst_id,
                static_rel.all_edges,
                True,
            )
        else:
            inherited_key_edges = _relation_key_edges_for_pair(llm_relation, src_id, dst_id, node_to_component)
            key_edges, all_edges = ground_relation_edges(inherited_key_edges, static_rel.all_edges)
            relation = Relation(
                relation=llm_relation.relation,
                src_name=id_to_name.get(src_id, src_id),
                dst_name=id_to_name.get(dst_id, dst_id),
                evidence=llm_relation.evidence,
                key_edges=key_edges,
                src_id=src_id,
                dst_id=dst_id,
                is_static=True,
                all_edges=all_edges,
            )
        global_relations[(src_id, dst_id)] = relation

    owner_index = build_owner_index(node_to_component)
    for llm_rel in llm_relations:
        pair = (llm_rel.src_id, llm_rel.dst_id)
        if llm_rel.src_id not in live_ids or llm_rel.dst_id not in live_ids:
            continue
        if pair in static_pairs or pair in superseded_llm_pairs:
            continue
        grounded = llm_rel.with_merged_edges()
        kept = [
            edge
            for edge in grounded.all_edges
            if edge_crosses_components(edge, owner_index, llm_rel.src_id, llm_rel.dst_id)
        ]
        kept_ids = {edge.identity() for edge in kept}
        global_relations[pair] = grounded.model_copy(
            update={"all_edges": kept, "key_edges": [e for e in grounded.key_edges if e.identity() in kept_ids]}
        )

    return drop_reverse_duplicates(
        drop_internal_self_relations(sorted(global_relations.values(), key=lambda rel: (rel.src_id, rel.dst_id)))
    )


def merge_relations(
    llm_relations: list[Relation],
    static_relations: list[ClusterRelation],
    analysis: AnalysisInsights,
) -> list[Relation]:
    """Merge LLM-generated relations with static analysis evidence.

    Static and LLM-provided edges are merged into one relation per component pair.
    Duplicate suppression applies inside key_edges/all_edges using source method,
    target method, and call-site set so multiple calls remain visible.
    """
    # Build name-to-id mapping
    name_to_id: dict[str, str] = {}
    for comp in analysis.components:
        if comp.name not in name_to_id:
            name_to_id[comp.name] = comp.component_id

    # Build id-to-name mapping (for static relations which use component_id)
    id_to_name: dict[str, str] = {comp.component_id: comp.name for comp in analysis.components}

    # Index static relations by (src_id, dst_id)
    static_by_ids: dict[tuple[str, str], ClusterRelation] = {}
    for sr in static_relations:
        static_by_ids[(sr.src_cluster_id, sr.dst_cluster_id)] = sr

    owner_index = build_owner_index(build_node_to_component_map(analysis))

    merged: list[Relation] = []
    matched_static_edge_ids: set[tuple] = set()

    for llm_rel in llm_relations:
        src_id = name_to_id.get(llm_rel.src_name, "")
        dst_id = name_to_id.get(llm_rel.dst_name, "")

        # Match static relation in the same direction only
        static_rel = static_by_ids.get((src_id, dst_id))
        static_edges = static_rel.all_edges if static_rel else []
        has_evidence = bool(llm_rel.evidence.strip())
        llm_key_edges = [
            edge for edge in llm_rel.key_edges if edge_crosses_components(edge, owner_index, src_id, dst_id)
        ]

        if not static_edges and not llm_key_edges and not has_evidence:
            continue
        if not static_edges and not llm_key_edges and has_evidence:
            logger.warning(
                "Keeping LLM-only relation without static or key-edge backing: %s -> %s (%s). Evidence: %s",
                llm_rel.src_name,
                llm_rel.dst_name,
                llm_rel.relation,
                llm_rel.evidence,
            )

        key_edges, all_edges = ground_relation_edges(llm_key_edges, static_edges)
        for edge in static_edges:
            matched_static_edge_ids.add((src_id, dst_id, edge.identity()))
        append_or_merge_relation(
            merged,
            Relation(
                relation=llm_rel.relation,
                src_name=llm_rel.src_name,
                dst_name=llm_rel.dst_name,
                evidence=llm_rel.evidence,
                key_edges=key_edges,
                src_id=src_id,
                dst_id=dst_id,
                is_static=bool(static_edges),
                all_edges=all_edges,
            ),
        )

    for static_rel in static_relations:
        src_name = id_to_name.get(static_rel.src_cluster_id, static_rel.src_cluster_id)
        dst_name = id_to_name.get(static_rel.dst_cluster_id, static_rel.dst_cluster_id)
        unmatched_edges = [
            edge
            for edge in static_rel.all_edges
            if (static_rel.src_cluster_id, static_rel.dst_cluster_id, edge.identity()) not in matched_static_edge_ids
        ]
        if unmatched_edges:
            append_or_merge_relation(
                merged,
                Relation.from_edges(
                    DEFAULT_STATIC_RELATION_LABEL,
                    src_name,
                    dst_name,
                    static_rel.src_cluster_id,
                    static_rel.dst_cluster_id,
                    unmatched_edges,
                    True,
                ),
            )

    merged = drop_reverse_duplicates(drop_internal_self_relations(merged))
    logger.info(
        f"Merged relations: {len(merged)} total "
        f"({sum(1 for relation in merged if relation.is_static)} static-backed, "
        f"{sum(1 for relation in merged if not relation.is_static)} LLM-only)"
    )
    return merged
