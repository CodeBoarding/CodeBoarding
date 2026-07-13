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
