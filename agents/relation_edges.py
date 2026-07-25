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

    The backing edges are real CFG calls whose endpoints currently fall in the same
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

    A relation restored verbatim keeps its call edges. If the only edges connecting the two
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
    return edge.source.qualified_name in changed_members or edge.target.qualified_name in changed_members


def _reconcile_unchanged_edges(fresh: Relation, previous: Relation, changed_members: set[str]) -> Relation:
    """Carry the baseline's edges forward for calls between two byte-identical methods.

    A call is written inside its source method's body, so an unchanged source cannot have
    changed which methods it calls; a rebuild that adds or drops an edge between two unchanged
    methods did so by re-attributing the graph, not because the code moved. Within a pair the
    fresh rebuild is trusted only for edges that touch a changed/added/deleted method (the
    structural truth); every edge between two unchanged methods is taken from the baseline.

    ``changed_members`` includes added and deleted qnames (present on exactly one side), so a
    baseline edge kept here can never cite a symbol that no longer exists — the deleted-symbol
    phantom is excluded at the source.
    """

    def split(fresh_edges: list[RelationEdge], baseline_edges: list[RelationEdge]) -> list[RelationEdge]:
        fresh_grounded = [e for e in fresh_edges if _edge_touches_changed_method(e, changed_members)]
        baseline_stable = [e for e in baseline_edges if not _edge_touches_changed_method(e, changed_members)]
        return Relation._unique_edges([*fresh_grounded, *baseline_stable])

    return fresh.model_copy(
        update={
            "all_edges": split(fresh.all_edges, previous.all_edges),
            "key_edges": split(fresh.key_edges, previous.key_edges),
        }
    )


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

    A baseline relation between two unchanged, still-live components that regeneration
    dropped is restored — but only when its backing edges still exist in live code. The
    deterministic rebuild drops a pair when the code connecting the two components was
    deleted; restoring it then would resurrect an edge citing a symbol that no longer
    exists. A fresh edge invented between two unchanged components is discarded, so
    structural drift against untouched components is eliminated too.

    Keyed on ``(src_id, dst_id)``, the stable component identity, not on displayed names.
    """

    def touches_change(src_id: str, dst_id: str) -> bool:
        return src_id in changed_component_ids or dst_id in changed_component_ids

    kept: list[Relation] = []
    rebuilt_pairs: set[tuple[str, str]] = set()
    for relation in rebuilt_relations:
        pair = (relation.src_id, relation.dst_id)
        rebuilt_pairs.add(pair)
        previous = baseline_by_pair.get(pair)
        if previous is None:
            # No baseline for this pair. A genuinely new connection into a changed area is
            # kept; a fresh edge invented between two untouched components is a re-attribution
            # artifact and is dropped.
            if touches_change(*pair):
                kept.append(relation)
            continue
        # Every edge between two byte-identical methods is taken from the baseline; only edges
        # that touch a changed/added/deleted method come from the fresh rebuild. This stops
        # re-attribution over untouched code from flipping edges, in touched and untouched
        # pairs alike.
        if changed_members is not None:
            relation = _reconcile_unchanged_edges(relation, previous, changed_members)
        # Keep the reader's wording unless a real, code-backed edge change re-worded it: an
        # untouched pair, or one whose edges came through unmoved, carries its label over.
        if not touches_change(*pair) or _relation_edges_unmoved(relation, previous):
            relation = relation.model_copy(update={"relation": previous.relation, "evidence": previous.evidence})
        kept.append(relation)
    for pair, relation in baseline_by_pair.items():
        if pair in rebuilt_pairs or touches_change(*pair) or pair[0] not in live_ids or pair[1] not in live_ids:
            continue
        if not _relation_backing_survives(relation, live_qnames):
            continue
        kept.append(relation)
    return drop_internal_self_relations(sorted(kept, key=lambda rel: (rel.src_id, rel.dst_id)))
