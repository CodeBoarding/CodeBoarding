"""
Static-analysis-based inter-component relationship building.

This module builds relationships between components from actual CFG (Call Flow Graph)
edges — no LLM needed.
"""

import logging
from collections import defaultdict
from collections.abc import Iterator
from dataclasses import dataclass, field

from agents.agent_responses import AnalysisInsights, Relation, RelationEdge, SourceCodeReference
from static_analyzer.graph import CallGraph, Edge

logger = logging.getLogger(__name__)


@dataclass
class ClusterRelation:
    """A relationship between two components derived from static CFG analysis."""

    src_cluster_id: str  # component's component_id, e.g. "1.2"
    dst_cluster_id: str  # e.g. "3"
    all_edges: list[RelationEdge] = field(default_factory=list)


def _call_sites_from_cfg_edge(edge: Edge) -> list[dict[str, int]]:
    return [
        {"line": int(call_site.get("line", 0)), "column": int(call_site.get("column", 0))}
        for call_site in edge.call_sites
    ]


def _relation_edge_from_cfg_edge(edge: Edge) -> RelationEdge:
    return RelationEdge(
        source=SourceCodeReference(
            qualified_name=edge.src_node.fully_qualified_name,
            reference_file=edge.src_node.file_path,
            reference_start_line=edge.src_node.line_start,
            reference_end_line=edge.src_node.line_end,
        ),
        target=SourceCodeReference(
            qualified_name=edge.dst_node.fully_qualified_name,
            reference_file=edge.dst_node.file_path,
            reference_start_line=edge.dst_node.line_start,
            reference_end_line=edge.dst_node.line_end,
        ),
        call_sites=_call_sites_from_cfg_edge(edge),
    )


def _edge_identity(
    edge: RelationEdge,
) -> tuple[str, str, str, str, int | None, int | None, int | None, int | None, tuple[tuple[int, int], ...]]:
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


def _merge_relation_edges(
    key_edges: list[RelationEdge], static_edges: list[RelationEdge]
) -> tuple[list[RelationEdge], list[RelationEdge]]:
    key_edges = _unique_relation_edges(key_edges)
    all_edges = _unique_relation_edges([*static_edges, *key_edges])
    return key_edges, all_edges


def _unique_relation_edges(edges: list[RelationEdge]) -> list[RelationEdge]:
    unique_edges: list[RelationEdge] = []
    seen: set[tuple] = set()
    for edge in edges:
        edge_id = _edge_identity(edge)
        if edge_id in seen:
            continue
        unique_edges.append(edge)
        seen.add(edge_id)
    return unique_edges


def _relation_with_edges(
    relation: str,
    src_name: str,
    dst_name: str,
    src_id: str,
    dst_id: str,
    edges: list[RelationEdge],
    is_static: bool,
    evidence: str = "",
) -> Relation:
    return Relation(
        relation=relation,
        src_name=src_name,
        dst_name=dst_name,
        evidence=evidence,
        key_edges=[],
        src_id=src_id,
        dst_id=dst_id,
        is_static=is_static,
        all_edges=_unique_relation_edges(edges),
    )


def _append_or_merge_relation(relations: list[Relation], relation: Relation) -> None:
    relation_id = (relation.src_id, relation.dst_id)
    for existing in relations:
        if (existing.src_id, existing.dst_id) != relation_id:
            continue
        existing.key_edges, existing.all_edges = _merge_relation_edges(
            [*existing.key_edges, *relation.key_edges], [*existing.all_edges, *relation.all_edges]
        )
        existing.is_static = existing.is_static or relation.is_static
        if not existing.evidence:
            existing.evidence = relation.evidence
        return
    relations.append(relation)


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
                edge_pairs[key].append(_relation_edge_from_cfg_edge(edge))

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

    merged: list[Relation] = []
    matched_static_edge_ids: set[tuple] = set()

    for llm_rel in llm_relations:
        src_id = name_to_id.get(llm_rel.src_name, "")
        dst_id = name_to_id.get(llm_rel.dst_name, "")

        # Match static relation in the same direction only
        static_rel = static_by_ids.get((src_id, dst_id))

        if static_rel:
            key_edges, all_edges = _merge_relation_edges(llm_rel.key_edges, static_rel.all_edges)
            for edge in static_rel.all_edges:
                matched_static_edge_ids.add((src_id, dst_id, _edge_identity(edge)))
            _append_or_merge_relation(
                merged,
                Relation(
                    relation=llm_rel.relation,
                    src_name=llm_rel.src_name,
                    dst_name=llm_rel.dst_name,
                    evidence=llm_rel.evidence,
                    key_edges=key_edges,
                    src_id=src_id,
                    dst_id=dst_id,
                    is_static=True,
                    all_edges=all_edges,
                ),
            )
        else:
            if llm_rel.key_edges:
                key_edges, all_edges = _merge_relation_edges(llm_rel.key_edges, [])
                _append_or_merge_relation(
                    merged,
                    Relation(
                        relation=llm_rel.relation,
                        src_name=llm_rel.src_name,
                        dst_name=llm_rel.dst_name,
                        evidence=llm_rel.evidence,
                        key_edges=key_edges,
                        src_id=src_id,
                        dst_id=dst_id,
                        is_static=False,
                        all_edges=all_edges,
                    ),
                )

    for static_rel in static_relations:
        src_name = id_to_name.get(static_rel.src_cluster_id, static_rel.src_cluster_id)
        dst_name = id_to_name.get(static_rel.dst_cluster_id, static_rel.dst_cluster_id)
        unmatched_edges = [
            edge
            for edge in static_rel.all_edges
            if (static_rel.src_cluster_id, static_rel.dst_cluster_id, _edge_identity(edge))
            not in matched_static_edge_ids
        ]
        if unmatched_edges:
            _append_or_merge_relation(
                merged,
                _relation_with_edges(
                    "calls",
                    src_name,
                    dst_name,
                    static_rel.src_cluster_id,
                    static_rel.dst_cluster_id,
                    unmatched_edges,
                    True,
                ),
            )

    logger.info(
        f"Merged relations: {len(merged)} total "
        f"({sum(1 for relation in merged if relation.is_static)} static-backed, "
        f"{sum(1 for relation in merged if not relation.is_static)} LLM-only)"
    )
    return merged
