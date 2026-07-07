from agents.agent_responses import Relation, RelationEdge

RelationEdgeIdentity = tuple[
    str, str, str, str, int | None, int | None, int | None, int | None, tuple[tuple[int, int], ...]
]


def relation_edge_identity(edge: RelationEdge) -> RelationEdgeIdentity:
    return edge.identity()


def unique_relation_edges(edges: list[RelationEdge]) -> list[RelationEdge]:
    return Relation._unique_edges(edges)


def merge_relation_edges(
    key_edges: list[RelationEdge], all_edges: list[RelationEdge]
) -> tuple[list[RelationEdge], list[RelationEdge]]:
    return Relation._merge_edges(key_edges, all_edges)


def relation_pair_key(relation: Relation, fallback_to_names: bool = False) -> tuple[str, str] | tuple[str, str, str]:
    return relation.pair_key(fallback_to_names=fallback_to_names)


def relation_with_merged_edges(
    relation: Relation,
    *,
    src_id: str | None = None,
    dst_id: str | None = None,
    src_name: str | None = None,
    dst_name: str | None = None,
) -> Relation:
    return relation.with_merged_edges(src_id=src_id, dst_id=dst_id, src_name=src_name, dst_name=dst_name)


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
    include_relation: bool = False,
) -> None:
    relation_to_add = relation_with_merged_edges(
        relation,
        src_id=src_id,
        dst_id=dst_id,
        src_name=src_name,
        dst_name=dst_name,
    )
    relation_id = key if key is not None else relation_to_add.pair_key(fallback_to_names, include_relation)
    for existing in relations:
        if existing.pair_key(fallback_to_names, include_relation) != relation_id:
            continue
        existing.merge_edges_from(relation_to_add)
        return
    relations.append(relation_to_add)


def merge_relations_by_pair(
    relations: list[Relation], fallback_to_names: bool = False, include_relation: bool = False
) -> list[Relation]:
    merged: list[Relation] = []
    for relation in relations:
        append_or_merge_relation(
            merged, relation, fallback_to_names=fallback_to_names, include_relation=include_relation
        )
    return merged
