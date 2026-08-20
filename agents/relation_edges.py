"""Merge component relations and index their source endpoints."""

from pathlib import Path

from agents.agent_responses import AnalysisInsights, Relation, RelationEdge
from repo_utils.path_utils import normalize_repo_path


def append_or_merge_relation(
    relations: list[Relation],
    relation: Relation,
    *,
    key: tuple[str, str] | None = None,
    include_relation: bool = False,
) -> None:
    relation_to_add = relation.with_merged_edges()
    relation_id = key if key is not None else relation_to_add.pair_key(include_relation)
    for existing in relations:
        if existing.pair_key(include_relation) != relation_id:
            continue
        existing.merge_edges_from(relation_to_add)
        return
    relations.append(relation_to_add)


def merge_relations_by_pair(relations: list[Relation], include_relation: bool = False) -> list[Relation]:
    merged: list[Relation] = []
    for relation in relations:
        append_or_merge_relation(merged, relation, include_relation=include_relation)
    return merged


def index_relation_endpoints(analysis: AnalysisInsights, repo_dir: Path) -> None:
    """Fill missing spans for relation endpoints already present in the file index."""
    spans_by_file: dict[str, dict[str, tuple[int, int]]] = {}
    for relation in analysis.components_relations:
        for edge in [*relation.key_edges, *relation.all_edges]:
            for reference in (edge.source, edge.target):
                if not reference.reference_file:
                    continue
                file_path = normalize_repo_path(reference.reference_file, repo_dir)
                file_spans = spans_by_file.setdefault(file_path, {})
                start_line, end_line = file_spans.get(reference.qualified_name, (0, 0))
                file_spans[reference.qualified_name] = (
                    start_line or reference.reference_start_line or 0,
                    end_line or reference.reference_end_line or 0,
                )

    for file_path, spans in spans_by_file.items():
        entry = analysis.files.get(file_path)
        if entry is not None:
            entry.merge_method_spans(spans)


def _is_internal_self_relation(relation: Relation) -> bool:
    """A component related to itself by concrete intra-component calls.

    Its supporting calls — the concrete method-to-method calls stored under the relation —
    are real CFG calls whose endpoints currently fall in the same
    component, so at this granularity there is no cross-component connection to draw — the
    loop is the residue of a rollup that collapsed a cross-child edge onto its shared parent.
    The CFG still holds every such edge, so expanding the component re-materialises it across
    the split children as a real cross-component relation. An edgeless self-relation (a
    runtime/config label with no backing call) is kept: nothing in the CFG would bring it
    back, so dropping it would lose evidence that lives nowhere else.
    """
    return (
        bool(relation.src_id) and relation.src_id == relation.dst_id and bool(relation.all_edges or relation.key_edges)
    )


def drop_internal_self_relations(relations: list[Relation]) -> list[Relation]:
    """Remove statically-backed self-loops from an assembled relation list (CFG untouched)."""
    return [relation for relation in relations if not _is_internal_self_relation(relation)]


def _relation_backing_survives(relation: Relation, live_qnames: set[str]) -> bool:
    """Whether a baseline relation's static backing still exists in live code.

    A relation is supported by concrete method-to-method calls; restoring it verbatim keeps them. If the only edges connecting the two
    components pointed at a symbol since deleted, restoring the relation resurrects an edge
    citing code that is gone — a phantom. A relation with no static edge at all is a
    runtime/config relation with nothing to invalidate, so it is left alone.
    """
    edges = relation.all_edges or relation.key_edges
    if not edges:
        return True
    return any(
        edge.source.qualified_name in live_qnames and edge.target.qualified_name in live_qnames for edge in edges
    )


def _backing_edge_pairs(relation: Relation) -> set[tuple[str, str]]:
    """The (source, target) method pairs supporting this relation — the calls under the arrow."""
    edges = relation.all_edges or relation.key_edges
    return {(edge.source.qualified_name, edge.target.qualified_name) for edge in edges}


def _relation_edges_unmoved(rebuilt: Relation, previous: Relation) -> bool:
    """True when a rebuilt pair carries the same call edges the baseline did.

    Compares the set of (source, target) qualified-name pairs, not call-site lines: a call
    that merely shifted rows still connects the same two symbols, so the architectural label
    the reader already saw is still accurate. An edgeless (runtime/config) pair returns False
    so it falls through to the endpoint-change gate rather than matching on an empty set.
    """
    rebuilt_edges = _backing_edge_pairs(rebuilt)
    return bool(rebuilt_edges) and rebuilt_edges == _backing_edge_pairs(previous)


def _edge_touches_changed_method(edge: RelationEdge, changed_members: set[str]) -> bool:
    return edge.source.qualified_name in changed_members


def _restore_baseline_orientation(relation: Relation, baseline_by_pair: dict) -> Relation:
    """Put an ungrounded relation back the way round the reader last saw it.

    Why only ungrounded ones: a statically-backed relation takes its direction from real CFG
    edges, so the arrow means something and must not be touched. A runtime/config relation has
    no call to fix it, so the model picks an orientation afresh each run — and a swap keyed on
    the ordered pair reads as one relation deleted and another added. On the reported case
    (CodeBoarding-action#65) two of the five changed edges were exactly that: the same two
    component pairs with their arrows reversed and relabelled.

    Applied only when the fresh relation carries no backing edges at all, because an edge is
    what would make the direction a claim rather than a phrasing — flipping a relation that has
    one would point its edges the wrong way.
    """
    if relation.is_static or relation.all_edges or relation.key_edges:
        return relation
    flipped = baseline_by_pair.get((relation.dst_id, relation.src_id))
    if flipped is None or flipped.is_static or flipped.all_edges or flipped.key_edges:
        return relation
    return relation.model_copy(
        update={
            "src_id": flipped.src_id,
            "dst_id": flipped.dst_id,
            "src_name": flipped.src_name,
            "dst_name": flipped.dst_name,
            "relation": flipped.relation,
            "evidence": flipped.evidence,
        }
    )


def _restore_baseline_wording(fresh: Relation, previous: Relation) -> Relation:
    """Carry the label, the evidence and the edge highlighting forward onto the fresh edges.

    Why: which edges are highlighted and their per-edge descriptions are re-rolled each run
    just like the label, so an unrelated commit rewrites them. They are rebound onto the fresh
    edge objects, never copied from the baseline, so spans and call sites stay current.
    """
    wording = {"relation": previous.relation, "evidence": previous.evidence}
    if not previous.key_edges:
        return fresh.model_copy(update=wording)
    highlighted = {
        (edge.source.qualified_name, edge.target.qualified_name): edge.description for edge in previous.key_edges
    }

    def annotate(edge: RelationEdge) -> RelationEdge:
        description = highlighted.get((edge.source.qualified_name, edge.target.qualified_name))
        return edge.model_copy(update={"description": description}) if description else edge

    # Membership follows the rebuild — a baseline highlight whose edge is gone stays gone —
    # but an edge the baseline highlighted and the rebuild still has is highlighted again.
    restored = [
        edge for edge in fresh.all_edges if (edge.source.qualified_name, edge.target.qualified_name) in highlighted
    ]
    return fresh.model_copy(
        update={
            **wording,
            "key_edges": Relation._unique_edges([*map(annotate, fresh.key_edges), *map(annotate, restored)]),
        }
    )


def _commit_deleted_the_backing(rebuilt: Relation, previous: Relation, changed_members: set[str]) -> bool:
    """Return whether a changed source removed every baseline backing edge.

    A call belongs to its source method, so only lost edges from changed sources prove deletion.
    """
    baseline_edges = previous.all_edges or previous.key_edges
    if not baseline_edges or rebuilt.all_edges or rebuilt.key_edges or rebuilt.evidence.strip():
        return False
    return any(_edge_touches_changed_method(edge, changed_members) for edge in baseline_edges)


def preserve_unchanged_relations(
    rebuilt_relations: list[Relation],
    baseline_by_pair: dict[tuple[str, str], Relation],
    changed_component_ids: set[str],
    live_ids: set[str],
    live_qnames: set[str],
    changed_members: set[str] | None = None,
) -> list[Relation]:
    """Keep the wording a reader already read for any relation whose call edges did not move.

    Regeneration re-derives every relation in its scope and re-words even the edges between
    components whose connection is unchanged, so a one-line diff relabels the whole diagram.
    The label carries forward whenever the pair's backing call edges are identical to the
    baseline; the call edges themselves always come from the fresh rebuild — they are the
    structural truth and must never go stale. A pair is re-worded only when its connection
    genuinely moved (an edge appeared, vanished, or repointed).

    The endpoint-change set alone is too coarse to gate wording: a component is flagged
    changed for any module-level edit to a file it merely co-owns, and clustering disperses
    a file's methods across the graph, so a two-method commit can flag most components while
    their inter-component edges are byte-identical. Gating on the pair's own edges instead of
    on its endpoints' change flags is what keeps those untouched connections stable.

    A pair whose changed source lost every baseline call is dropped unless the rebuild supplies
    alternate runtime/config evidence.

    A baseline relation between two unchanged, still-live components that regeneration
    dropped is restored — but only when its backing edges still exist in live code. The
    deterministic rebuild drops a pair when the code connecting the two components was
    deleted; restoring it then would resurrect an edge citing a symbol that no longer
    exists.

    Wording is all this layer carries; CFG-backed edges always come from the fresh rebuild.
    Why: extraction is deterministic, and a baseline edge carried forward keeps the line
    numbers its call sites had when it was written.

    Keyed on ``(src_id, dst_id)``, the stable component identity, not on displayed names.
    """

    def touches_change(src_id: str, dst_id: str) -> bool:
        return src_id in changed_component_ids or dst_id in changed_component_ids

    kept: list[Relation] = []
    rebuilt_pairs: set[tuple[str, str]] = set()
    for relation in rebuilt_relations:
        if (relation.src_id, relation.dst_id) not in baseline_by_pair:
            relation = _restore_baseline_orientation(relation, baseline_by_pair)
        pair = (relation.src_id, relation.dst_id)
        rebuilt_pairs.add(pair)
        previous = baseline_by_pair.get(pair)
        if previous is None:
            # No baseline for this pair. A genuinely new connection into a changed area is
            # kept; a fresh edge invented between two untouched components is a re-attribution
            # artifact and is dropped.
            if not touches_change(*pair):
                continue
            # The pair asserts no connection at all.
            if not relation.all_edges and not relation.key_edges and not relation.evidence.strip():
                continue
            kept.append(relation)
            continue
        if changed_members is not None and _commit_deleted_the_backing(relation, previous, changed_members):
            continue
        # Keep the reader's wording unless this pair's own supporting calls moved.
        #
        # The supporting calls are the concrete method-to-method calls under the relation — the
        # ones that show WHERE one component reaches the other. They are the only evidence that
        # the connection itself changed, so they are what decides whether it may be re-worded.
        # Gating on the endpoint components' change flags instead re-words far more: a component
        # is flagged for any edit to a file it merely co-owns, so deleting one function re-worded
        # 17 of the relations in `referenced-symbol-deleted` whose calls had not moved at all.
        # A pair with no supporting calls has nothing that could have moved, so its wording is
        # kept too — an edgeless runtime relation is prose, and re-rolling prose is the churn.
        if (
            not touches_change(*pair)
            or _relation_edges_unmoved(relation, previous)
            or (not _backing_edge_pairs(relation) and not relation.evidence.strip())
        ):
            relation = _restore_baseline_wording(relation, previous)
        kept.append(relation)
    for pair, relation in baseline_by_pair.items():
        if pair in rebuilt_pairs or touches_change(*pair) or pair[0] not in live_ids or pair[1] not in live_ids:
            continue
        if not _relation_backing_survives(relation, live_qnames):
            continue
        kept.append(relation)
    return drop_internal_self_relations(sorted(kept, key=lambda rel: (rel.src_id, rel.dst_id)))
