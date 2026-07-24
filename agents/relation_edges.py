"""Merge component relations and index their source endpoints."""

from pathlib import Path

from agents.agent_responses import AnalysisInsights, Relation
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


def preserve_unchanged_relations(
    rebuilt_relations: list[Relation],
    baseline_by_pair: dict[tuple[str, str], Relation],
    changed_component_ids: set[str],
    live_ids: set[str],
    live_qnames: set[str],
) -> list[Relation]:
    """Keep the wording a reader already read for any relation whose endpoints did not move.

    Regeneration re-derives every relation in its scope and re-words even the edges between
    two untouched components, so a one-line diff relabels the whole diagram. For a pair
    neither of whose endpoints changed, the call edges come from the fresh rebuild — they
    are the structural truth and must never go stale — while the label and evidence come
    from the baseline. Pairs touching a changed component keep the fresh version outright.

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
        if touches_change(*pair):
            kept.append(relation)
            continue
        previous = baseline_by_pair.get(pair)
        if previous is not None:
            kept.append(relation.model_copy(update={"relation": previous.relation, "evidence": previous.evidence}))
    for pair, relation in baseline_by_pair.items():
        if pair in rebuilt_pairs or touches_change(*pair) or pair[0] not in live_ids or pair[1] not in live_ids:
            continue
        if not _relation_backing_survives(relation, live_qnames):
            continue
        kept.append(relation)
    return sorted(kept, key=lambda rel: (rel.src_id, rel.dst_id))
