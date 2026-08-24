"""Merge component relations and index their source endpoints."""

from collections.abc import Callable
from pathlib import Path

from agents.agent_responses import AnalysisInsights, Relation, RelationEdge, SourceCodeReference
from clustering_ids import is_self_or_descendant
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


def ground_relation_edges(
    llm_key_edges: list[RelationEdge], static_edges: list[RelationEdge]
) -> tuple[list[RelationEdge], list[RelationEdge]]:
    """Ground an LLM relation's key edges in deterministic static edges."""
    static_unique = Relation.unique_edges(static_edges)
    if not static_unique:
        merged = Relation.unique_edges(llm_key_edges)
        return merged, merged
    highlighted: list[RelationEdge] = []
    for static_edge in static_unique:
        match = next(
            (
                key
                for key in llm_key_edges
                if _qualified_names_match(key.source.qualified_name, static_edge.source.qualified_name)
                and _qualified_names_match(key.target.qualified_name, static_edge.target.qualified_name)
            ),
            None,
        )
        if match is None:
            continue
        highlighted.append(
            static_edge.model_copy(update={"description": match.description}) if match.description else static_edge
        )
    return highlighted, static_unique


def edge_crosses_components(
    edge: RelationEdge,
    owner_of: Callable[[SourceCodeReference], str],
    src_id: str,
    dst_id: str,
) -> bool:
    """Return whether an edge's owned endpoints match its declared component pair."""
    for reference, component_id in ((edge.source, src_id), (edge.target, dst_id)):
        owner = owner_of(reference)
        if owner and component_id and not is_self_or_descendant(owner, component_id):
            return False
    return True


def prune_ungrounded_edges(
    relations: list[Relation],
    owner_of: Callable[[SourceCodeReference], str],
    keep_edge: Callable[[RelationEdge], bool],
    changed_members: set[str] | None = None,
) -> list[Relation]:
    """Re-apply edge filters after baseline preservation, moving misfiled edges when possible."""
    by_pair: dict[tuple[str, str], Relation] = {(relation.src_id, relation.dst_id): relation for relation in relations}
    additions: dict[tuple[str, str], list[RelationEdge]] = {}

    def find_relation_pair_for_edge(edge: RelationEdge) -> tuple[tuple[str, str] | None, bool]:
        source = owner_of(edge.source)
        target = owner_of(edge.target)
        if not source or not target:
            return None, False
        if is_self_or_descendant(source, target) or is_self_or_descendant(target, source):
            return None, True
        for pair in by_pair:
            if is_self_or_descendant(source, pair[0]) and is_self_or_descendant(target, pair[1]):
                return pair, False
        return None, False

    kept: list[Relation] = []
    for relation in relations:
        pair = (relation.src_id, relation.dst_id)
        all_edges: list[RelationEdge] = []
        for edge in relation.all_edges:
            if not keep_edge(edge):
                continue
            if edge_crosses_components(edge, owner_of, relation.src_id, relation.dst_id):
                all_edges.append(edge)
                continue
            if changed_members is not None and edge.source.qualified_name not in changed_members:
                all_edges.append(edge)
                continue
            matching_pair, is_internal = find_relation_pair_for_edge(edge)
            if is_internal:
                continue
            if matching_pair is None or matching_pair == pair:
                all_edges.append(edge)
            else:
                additions.setdefault(matching_pair, []).append(edge)
        surviving = {edge.identity() for edge in all_edges}
        key_edges = [edge for edge in relation.key_edges if edge.identity() in surviving]
        kept.append(relation.model_copy(update={"all_edges": all_edges, "key_edges": key_edges}))

    settled: list[Relation] = []
    for relation in kept:
        moved = additions.get((relation.src_id, relation.dst_id))
        if moved:
            relation = relation.model_copy(update={"all_edges": Relation.unique_edges([*relation.all_edges, *moved])})
        if not relation.all_edges and not relation.key_edges and not relation.evidence.strip():
            continue
        settled.append(relation)

    return drop_reverse_duplicates(settled, changed_members)


def drop_reverse_duplicates(relations: list[Relation], changed_members: set[str] | None = None) -> list[Relation]:
    """Collapse an ungrounded relation duplicated in the opposite direction."""
    grounded_pairs = {
        (relation.src_id, relation.dst_id)
        for relation in relations
        if (relation.all_edges or relation.key_edges)
        and (
            changed_members is None
            or any(edge.source.qualified_name in changed_members for edge in [*relation.all_edges, *relation.key_edges])
        )
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
            continue
        other = by_pair.get(reverse) if reverse != pair else None
        if (
            changed_members is None
            and other is not None
            and not (other.all_edges or other.key_edges or other.is_static)
        ):
            loser = max((relation, other), key=lambda item: (-len(item.evidence or ""), item.src_id, item.dst_id))
            if (loser.src_id, loser.dst_id) == pair and reverse not in dropped_bare:
                dropped_bare.add(pair)
                continue
        kept.append(relation)
    return kept


def _qualified_names_match(first: str, second: str) -> bool:
    """Return whether canonical or suffix-qualified names denote the same symbol."""
    first, second = first.replace(":", "."), second.replace(":", ".")
    return first == second or first.endswith(f".{second}") or second.endswith(f".{first}")


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


def _filter_edges_touched_by_change(relation: Relation, changed_members: set[str]) -> Relation:
    """Keep only the edges a code change can account for, for a pair with no baseline.

    Why: a call lives in its source method's body, so an edge can only appear when one of its
    endpoints changed. ``_reconcile_unchanged_edges`` enforces that by falling back to the
    baseline, but re-clustering invents component-pairs that have no baseline to fall back to —
    there every re-attributed edge would otherwise enter as if the commit had written it.
    """
    return relation.model_copy(
        update={
            "all_edges": [e for e in relation.all_edges if _edge_touches_changed_method(e, changed_members)],
            "key_edges": [e for e in relation.key_edges if _edge_touches_changed_method(e, changed_members)],
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
        return Relation.unique_edges([*fresh_grounded, *baseline_stable])

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

    A pair whose changed source lost every baseline call is dropped unless the rebuild supplies
    alternate runtime/config evidence.

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
            if changed_members is not None:
                relation = _filter_edges_touched_by_change(relation, changed_members)
                # Nothing survived and nothing else is claimed: the pair asserts no connection.
                if not relation.all_edges and not relation.key_edges and not relation.evidence.strip():
                    continue
            kept.append(relation)
            continue
        # Every edge between two byte-identical methods is taken from the baseline; only edges
        # that touch a changed/added/deleted method come from the fresh rebuild. This stops
        # re-attribution over untouched code from flipping edges, in touched and untouched
        # pairs alike.
        if changed_members is not None:
            relation = _reconcile_unchanged_edges(relation, previous, changed_members)
            if _commit_deleted_the_backing(relation, previous, changed_members):
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
            relation = relation.model_copy(update={"relation": previous.relation, "evidence": previous.evidence})
        kept.append(relation)
    for pair, relation in baseline_by_pair.items():
        if pair in rebuilt_pairs or touches_change(*pair) or pair[0] not in live_ids or pair[1] not in live_ids:
            continue
        if not _relation_backing_survives(relation, live_qnames):
            continue
        kept.append(relation)
    return drop_internal_self_relations(sorted(kept, key=lambda rel: (rel.src_id, rel.dst_id)))
