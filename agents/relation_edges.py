"""Merge component relations and index their source endpoints."""

from pathlib import Path

from agents.agent_responses import AnalysisInsights, Relation
from agents.file_index_models import FileEntry, MethodEntry
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
    """Add relation endpoint references to the analysis file index."""
    methods_by_file: dict[str, dict[str, MethodEntry]] = {}
    for relation in analysis.components_relations:
        for edge in [*relation.key_edges, *relation.all_edges]:
            for reference in (edge.source, edge.target):
                if not reference.reference_file:
                    continue
                file_path = normalize_repo_path(reference.reference_file, repo_dir)
                entry = analysis.files.setdefault(file_path, FileEntry())
                methods_by_qname = methods_by_file.setdefault(
                    file_path,
                    {method.qualified_name: method for method in entry.methods},
                )
                indexed = methods_by_qname.get(reference.qualified_name)
                if indexed is None:
                    indexed = MethodEntry(
                        qualified_name=reference.qualified_name,
                        start_line=reference.reference_start_line or 0,
                        end_line=reference.reference_end_line or 0,
                        node_type="REFERENCE",
                    )
                    entry.methods.append(indexed)
                    methods_by_qname[reference.qualified_name] = indexed
                elif indexed.node_type == "REFERENCE":
                    indexed.start_line = indexed.start_line or reference.reference_start_line or 0
                    indexed.end_line = indexed.end_line or reference.reference_end_line or 0

    for entry in analysis.files.values():
        entry.methods.sort(key=lambda method: (method.start_line, method.end_line, method.qualified_name))
