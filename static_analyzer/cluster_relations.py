"""
Static-analysis-based inter-component relationship building.

This module builds relationships between components from actual CFG (Call Flow Graph)
edges — no LLM needed.
"""

import logging
from collections import defaultdict
from collections.abc import Iterator
from dataclasses import dataclass, field

from constants import DEFAULT_STATIC_RELATION_LABEL
from agents.agent_responses import AnalysisInsights, Relation, RelationEdge
from agents.component_ownership import ComponentOwnershipIndex
from agents.relation_edges import (
    drop_internal_self_relations,
    drop_reverse_duplicates,
    edge_crosses_components,
    ground_relation_edges,
)
from static_analyzer.cfg import CallGraph

logger = logging.getLogger(__name__)


@dataclass
class ClusterRelation:
    """A relationship between two components derived from static CFG analysis."""

    src_cluster_id: str  # component's component_id, e.g. "1.2"
    dst_cluster_id: str  # e.g. "3"
    all_edges: list[RelationEdge] = field(default_factory=list)


def build_global_node_to_component_map(
    root_analysis: AnalysisInsights,
    sub_analyses: dict[str, AnalysisInsights],
) -> dict[str, str]:
    """Map each node to the deepest currently expanded component that owns it."""
    node_to_component = root_analysis.node_owners()
    for parent_id, sub_analysis in sorted(sub_analyses.items(), key=lambda item: (item[0].count("."), item[0])):
        for node_name, component_id in sub_analysis.node_owners().items():
            if component_id.startswith(f"{parent_id}."):
                node_to_component[node_name] = component_id
    return node_to_component


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

    ownership = ComponentOwnershipIndex.from_node_owners(node_to_component)

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
            if edge_crosses_components(edge, ownership.owner_of, llm_rel.src_id, llm_rel.dst_id)
        ]
        kept_ids = {edge.identity() for edge in kept}
        global_relations[pair] = grounded.model_copy(
            update={"all_edges": kept, "key_edges": [e for e in grounded.key_edges if e.identity() in kept_ids]}
        )

    return drop_reverse_duplicates(
        drop_internal_self_relations(sorted(global_relations.values(), key=lambda rel: (rel.src_id, rel.dst_id)))
    )
