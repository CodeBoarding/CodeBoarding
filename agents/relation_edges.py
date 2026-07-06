from agents.agent_responses import Relation, RelationEdge

RelationEdgeIdentity = tuple[
    str, str, str, str, int | None, int | None, int | None, int | None, tuple[tuple[int, int], ...]
]


def relation_edge_identity(edge: RelationEdge) -> RelationEdgeIdentity:
    return (
        edge.source.qualified_name,
        edge.target.qualified_name,
        edge.source.reference_file or "",
        edge.target.reference_file or "",
        edge.source.reference_start_line,
        edge.source.reference_end_line,
        edge.target.reference_start_line,
        edge.target.reference_end_line,
        tuple(sorted((int(site.get("line", 0)), int(site.get("column", 0))) for site in edge.call_sites)),
    )


def unique_relation_edges(edges: list[RelationEdge]) -> list[RelationEdge]:
    unique_edges: list[RelationEdge] = []
    seen: set[RelationEdgeIdentity] = set()
    for edge in edges:
        edge_id = relation_edge_identity(edge)
        if edge_id in seen:
            continue
        unique_edges.append(edge)
        seen.add(edge_id)
    return unique_edges


def merge_relation_edges(
    key_edges: list[RelationEdge], all_edges: list[RelationEdge]
) -> tuple[list[RelationEdge], list[RelationEdge]]:
    merged_key_edges = unique_relation_edges(key_edges)
    merged_all_edges = unique_relation_edges([*all_edges, *merged_key_edges])
    return merged_key_edges, merged_all_edges


def relation_pair_key(relation: Relation, fallback_to_names: bool = False) -> tuple[str, str]:
    if fallback_to_names:
        return (relation.src_id or relation.src_name, relation.dst_id or relation.dst_name)
    return (relation.src_id, relation.dst_id)


def relation_with_merged_edges(
    relation: Relation,
    *,
    src_id: str | None = None,
    dst_id: str | None = None,
    src_name: str | None = None,
    dst_name: str | None = None,
) -> Relation:
    key_edges, all_edges = merge_relation_edges(relation.key_edges, relation.all_edges)
    return Relation(
        relation=relation.relation,
        src_name=src_name if src_name is not None else relation.src_name,
        dst_name=dst_name if dst_name is not None else relation.dst_name,
        evidence=relation.evidence,
        key_edges=key_edges,
        src_id=src_id if src_id is not None else relation.src_id,
        dst_id=dst_id if dst_id is not None else relation.dst_id,
        is_static=relation.is_static,
        all_edges=all_edges,
    )


def append_or_merge_relation(
    relations: list[Relation],
    relation: Relation,
    *,
    key: tuple[str, str] | None = None,
    src_id: str | None = None,
    dst_id: str | None = None,
    src_name: str | None = None,
    dst_name: str | None = None,
    fallback_to_names: bool = False,
) -> None:
    relation_to_add = relation_with_merged_edges(
        relation,
        src_id=src_id,
        dst_id=dst_id,
        src_name=src_name,
        dst_name=dst_name,
    )
    relation_id = key if key is not None else relation_pair_key(relation_to_add, fallback_to_names)
    for existing in relations:
        if relation_pair_key(existing, fallback_to_names) != relation_id:
            continue
        existing.key_edges, existing.all_edges = merge_relation_edges(
            [*existing.key_edges, *relation_to_add.key_edges], [*existing.all_edges, *relation_to_add.all_edges]
        )
        existing.is_static = existing.is_static or relation_to_add.is_static
        if not existing.evidence:
            existing.evidence = relation_to_add.evidence
        return
    relations.append(relation_to_add)


def merge_relations_by_pair(relations: list[Relation], fallback_to_names: bool = False) -> list[Relation]:
    merged: list[Relation] = []
    for relation in relations:
        append_or_merge_relation(merged, relation, fallback_to_names=fallback_to_names)
    return merged
