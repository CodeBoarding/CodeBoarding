"""
Static-analysis-based inter-component relationship building.

This module builds relationships between components from actual CFG (Call Flow Graph)
edges — no LLM needed.
"""

import logging
from collections import defaultdict
from dataclasses import dataclass, field

from agents.agent_responses import AnalysisInsights, Relation
from static_analyzer.graph import CallGraph

logger = logging.getLogger(__name__)


@dataclass
class ClusterRelation:
    """A relationship between two components derived from static CFG analysis."""

    src_cluster_id: str  # component's component_id, e.g. "1.2"
    dst_cluster_id: str  # e.g. "3"
    edge_count: int = 0  # number of CFG edges crossing this boundary
    sample_edges: list[tuple[str, str]] = field(default_factory=list)  # representative (src, dst) node pairs


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
    max_samples: int = 5,
) -> list[ClusterRelation]:
    """Build inter-component relations from actual CFG edges.

    For every CFG edge where src and dst belong to different components,
    count and collect sample edges.

    Args:
        node_to_component: Mapping from node qualified_name to component_id.
        cfg_graphs: Mapping from language to CallGraph.
        max_samples: Maximum number of sample edges to collect per relation.

    Returns:
        List of ClusterRelation objects, one per (src_component, dst_component) pair.
    """
    edge_pairs: dict[tuple[str, str], list[tuple[str, str]]] = defaultdict(list)

    for cfg in cfg_graphs.values():
        for edge in cfg.edges:
            src_name = edge.get_source()
            dst_name = edge.get_destination()
            src_comp = node_to_component.get(src_name)
            dst_comp = node_to_component.get(dst_name)
            if src_comp and dst_comp and src_comp != dst_comp:
                edge_pairs[(src_comp, dst_comp)].append((src_name, dst_name))

    relations = []
    for (src_c, dst_c), edges in sorted(edge_pairs.items()):
        relations.append(
            ClusterRelation(
                src_cluster_id=src_c,
                dst_cluster_id=dst_c,
                edge_count=len(edges),
                sample_edges=edges[:max_samples],
            )
        )

    logger.info(f"Built {len(relations)} static inter-component relations from CFG edges")
    return relations


def build_global_node_to_component_map(
    root_analysis: AnalysisInsights,
    sub_analyses: dict[str, AnalysisInsights],
) -> dict[str, str]:
    """Map every node to its deepest component_id across the entire hierarchy.

    Walks all sub-analyses and maps each node's qualified_name to the most specific
    (deepest) component it belongs to. This enables cross-boundary relation detection
    between components at different hierarchy levels (e.g., "2.1" -> "3.5.2").
    """
    node_to_component: dict[str, str] = {}

    def collect_from_analysis(analysis: AnalysisInsights) -> None:
        for comp in analysis.components:
            # If this component has a sub-analysis, its children provide deeper mappings.
            # We still record the parent mapping first; children will overwrite with deeper IDs.
            for fg in comp.file_methods:
                for method in fg.methods:
                    node_to_component[method.qualified_name] = comp.component_id

            if comp.component_id in sub_analyses:
                collect_from_analysis(sub_analyses[comp.component_id])

    collect_from_analysis(root_analysis)
    return node_to_component


def build_global_relations(
    root_analysis: AnalysisInsights,
    sub_analyses: dict[str, AnalysisInsights],
    cfg_graphs: dict[str, CallGraph],
) -> list[Relation]:
    """Build cross-boundary relations at the deepest available granularity.

    Uses the full CFG with a global node-to-component map to find ALL edges between
    components at any depth. This captures relationships like "2.1" -> "3.5.2" that
    per-level analysis cannot see.

    LLM-generated labels from all levels are matched where possible; otherwise
    static-only relations get the auto-label "calls".
    """
    # Build global map: node -> deepest component_id
    global_map = build_global_node_to_component_map(root_analysis, sub_analyses)

    # Find all inter-component edges using the full CFG
    static_relations = build_component_relations(global_map, cfg_graphs)

    # Collect all LLM-generated relations from every level for label matching
    all_llm_relations: list[Relation] = list(root_analysis.components_relations)
    for sub_analysis in sub_analyses.values():
        all_llm_relations.extend(sub_analysis.components_relations)

    # Build id-to-name map across all levels
    id_to_name: dict[str, str] = {}

    def collect_names(analysis: AnalysisInsights) -> None:
        for comp in analysis.components:
            id_to_name[comp.component_id] = comp.name
            if comp.component_id in sub_analyses:
                collect_names(sub_analyses[comp.component_id])

    collect_names(root_analysis)

    # Index LLM relations by (src_id, dst_id) for label matching.
    # A parent-level relation like "1" -> "2" should provide labels for
    # child relations like "1.1" -> "2.3". We match by checking if the
    # static relation's src/dst are descendants of the LLM relation's src/dst.
    llm_by_ids: dict[tuple[str, str], Relation] = {}
    for rel in all_llm_relations:
        llm_by_ids[(rel.src_id, rel.dst_id)] = rel

    def find_llm_label(src_id: str, dst_id: str) -> str | None:
        """Find the best LLM label for a static relation, checking ancestors."""
        # Direct match
        if (src_id, dst_id) in llm_by_ids:
            return llm_by_ids[(src_id, dst_id)].relation

        # Check ancestor combinations: e.g., for "1.2.3" -> "4.5",
        # try "1.2" -> "4", "1" -> "4", etc.
        src_parts = src_id.split(".")
        dst_parts = dst_id.split(".")
        for si in range(len(src_parts), 0, -1):
            src_ancestor = ".".join(src_parts[:si])
            for di in range(len(dst_parts), 0, -1):
                dst_ancestor = ".".join(dst_parts[:di])
                if (src_ancestor, dst_ancestor) in llm_by_ids:
                    return llm_by_ids[(src_ancestor, dst_ancestor)].relation
        return None

    result: list[Relation] = []
    for sr in static_relations:
        label = find_llm_label(sr.src_cluster_id, sr.dst_cluster_id)
        result.append(
            Relation(
                relation=label or "calls",
                src_name=id_to_name.get(sr.src_cluster_id, sr.src_cluster_id),
                dst_name=id_to_name.get(sr.dst_cluster_id, sr.dst_cluster_id),
                src_id=sr.src_cluster_id,
                dst_id=sr.dst_cluster_id,
                edge_count=sr.edge_count,
                is_static=True,
            )
        )

    # Also include LLM-only relations (no static backing) — these are architectural
    # relations the LLM identified that may not have direct CFG edges.
    static_keys = {(sr.src_cluster_id, sr.dst_cluster_id) for sr in static_relations}
    for rel in all_llm_relations:
        if (rel.src_id, rel.dst_id) not in static_keys:
            # Check this isn't a parent-level relation superseded by a child-level one.
            # E.g., skip "1" -> "2" if "1.1" -> "2.3" exists in static_keys.
            is_superseded = (
                any(
                    src.startswith(rel.src_id + ".") or src == rel.src_id
                    for src, dst in static_keys
                    if dst.startswith(rel.dst_id + ".") or dst == rel.dst_id
                )
                if rel.src_id and rel.dst_id
                else False
            )
            if not is_superseded:
                result.append(
                    Relation(
                        relation=rel.relation,
                        src_name=rel.src_name,
                        dst_name=rel.dst_name,
                        src_id=rel.src_id,
                        dst_id=rel.dst_id,
                        edge_count=0,
                        is_static=False,
                    )
                )

    logger.info(f"Built {len(result)} global relations ({len(static_relations)} static, rest LLM-only)")
    return result


def merge_relations(
    llm_relations: list[Relation],
    static_relations: list[ClusterRelation],
    analysis: AnalysisInsights,
) -> list[Relation]:
    """Merge LLM-generated relations with static analysis evidence.

    Strategy (Supplement):
    - Static + matching LLM: keep LLM's human-readable label, attach edge_count, is_static=True
    - LLM only (no static backing): keep with is_static=False
    - Static only (no LLM label): add with auto-generated label "calls" + edge_count

    Matching is done by component ID in the same direction (src -> dst).
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
    matched_static_keys: set[tuple[str, str]] = set()

    for llm_rel in llm_relations:
        src_id = name_to_id.get(llm_rel.src_name, "")
        dst_id = name_to_id.get(llm_rel.dst_name, "")

        # Match static relation in the same direction only
        static_rel = static_by_ids.get((src_id, dst_id))

        if static_rel:
            # LLM relation backed by static evidence — keep with static info
            merged.append(
                Relation(
                    relation=llm_rel.relation,
                    src_name=llm_rel.src_name,
                    dst_name=llm_rel.dst_name,
                    src_id=src_id,
                    dst_id=dst_id,
                    edge_count=static_rel.edge_count,
                    is_static=True,
                )
            )
            matched_static_keys.add((static_rel.src_cluster_id, static_rel.dst_cluster_id))
        else:
            # LLM relation with no static backing — keep as LLM-only
            merged.append(
                Relation(
                    relation=llm_rel.relation,
                    src_name=llm_rel.src_name,
                    dst_name=llm_rel.dst_name,
                    src_id=src_id,
                    dst_id=dst_id,
                    edge_count=0,
                    is_static=False,
                )
            )

    # Add static relations that weren't matched by any LLM relation
    for (src_id, dst_id), sr in static_by_ids.items():
        if (src_id, dst_id) not in matched_static_keys:
            src_name = id_to_name.get(src_id, src_id)
            dst_name = id_to_name.get(dst_id, dst_id)
            merged.append(
                Relation(
                    relation="calls",
                    src_name=src_name,
                    dst_name=dst_name,
                    src_id=src_id,
                    dst_id=dst_id,
                    edge_count=sr.edge_count,
                    is_static=True,
                )
            )

    logger.info(
        f"Merged relations: {len(merged)} total "
        f"({len(matched_static_keys)} LLM+static, "
        f"{len(merged) - len(matched_static_keys) - (len(llm_relations) - len(matched_static_keys))} static-only, "
        f"{len(llm_relations) - len(matched_static_keys)} LLM-only)"
    )
    return merged
