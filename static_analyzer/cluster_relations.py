"""
Static-analysis-based inter-component relationship building.

This module builds relationships between components from actual CFG (Call Flow Graph)
edges — no LLM needed.
"""

import logging
from collections import defaultdict
from collections.abc import Iterator
from dataclasses import dataclass, field

from agents.agent_responses import AnalysisInsights, BridgeEdge, Relation
from static_analyzer.graph import CallGraph, Edge

logger = logging.getLogger(__name__)


@dataclass
class ClusterRelation:
    """A relationship between two components derived from static CFG analysis."""

    src_cluster_id: str  # component's component_id, e.g. "1.2"
    dst_cluster_id: str  # e.g. "3"
    edge_count: int = 0  # number of CFG edges crossing this boundary
    bridge_edges: list[BridgeEdge] = field(default_factory=list)


def _bridge_edge_from_cfg_edge(edge: Edge) -> BridgeEdge:
    return BridgeEdge(
        src_qualified_name=edge.src_node.fully_qualified_name,
        dst_qualified_name=edge.dst_node.fully_qualified_name,
        src_file=edge.src_node.file_path,
        dst_file=edge.dst_node.file_path,
        src_start_line=edge.src_node.line_start,
        src_end_line=edge.src_node.line_end,
        dst_start_line=edge.dst_node.line_start,
        dst_end_line=edge.dst_node.line_end,
    )


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
    edge_pairs: dict[tuple[str, str], list[BridgeEdge]] = defaultdict(list)
    edge_counts: dict[tuple[str, str], int] = defaultdict(int)

    for cfg in cfg_graphs.values():
        for edge in cfg.edges:
            src_name = edge.get_source()
            dst_name = edge.get_destination()
            src_comp = node_to_component.get(src_name)
            dst_comp = node_to_component.get(dst_name)
            if src_comp and dst_comp and src_comp != dst_comp:
                key = (src_comp, dst_comp)
                edge_counts[key] += 1
                edge_pairs[key].append(_bridge_edge_from_cfg_edge(edge))

    relations = []
    for (src_c, dst_c), edges in sorted(edge_pairs.items()):
        relations.append(
            ClusterRelation(
                src_cluster_id=src_c,
                dst_cluster_id=dst_c,
                edge_count=edge_counts[(src_c, dst_c)],
                bridge_edges=edges,
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


def _collect_id_to_name(
    root_analysis: AnalysisInsights,
    sub_analyses: dict[str, AnalysisInsights],
) -> dict[str, str]:
    """Map component_id -> name across the root and every nested sub-analysis."""
    id_to_name: dict[str, str] = {}
    stack: list[AnalysisInsights] = [root_analysis]
    while stack:
        analysis = stack.pop()
        for comp in analysis.components:
            id_to_name[comp.component_id] = comp.name
            if comp.component_id in sub_analyses:
                stack.append(sub_analyses[comp.component_id])
    return id_to_name


def iter_ancestor_ids(component_id: str) -> Iterator[str]:
    """Yield component_id then each shorter dotted-prefix ancestor, longest first.

    ``"1.2.3"`` -> ``"1.2.3"``, ``"1.2"``, ``"1"``.
    """
    parts = component_id.split(".")
    for i in range(len(parts), 0, -1):
        yield ".".join(parts[:i])


def is_self_or_descendant(component_id: str, ancestor_id: str) -> bool:
    """True when *component_id* is *ancestor_id* itself or a dotted descendant of it.

    ``("1.2", "1")`` and ``("1", "1")`` -> True; ``("10", "1")`` -> False.
    """
    return component_id == ancestor_id or component_id.startswith(f"{ancestor_id}.")


def find_llm_label(src_id: str, dst_id: str, llm_by_ids: dict[tuple[str, str], Relation]) -> str | None:
    """Best LLM label for a static edge: direct (src_id, dst_id), else nearest ancestor pair.

    A parent-level relation like ``"1" -> "2"`` supplies the label for a finer
    static edge like ``"1.1" -> "2.3"``. Ancestors are tried deepest-first on
    both endpoints so the most specific declared label wins.
    """
    for src_ancestor in iter_ancestor_ids(src_id):
        for dst_ancestor in iter_ancestor_ids(dst_id):
            rel = llm_by_ids.get((src_ancestor, dst_ancestor))
            if rel is not None:
                return rel.relation
    return None


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
    id_to_name = _collect_id_to_name(root_analysis, sub_analyses)

    # Index LLM relations by (src_id, dst_id) so find_llm_label can match a
    # static edge directly or via the nearest ancestor pair.
    llm_by_ids: dict[tuple[str, str], Relation] = {}
    for rel in all_llm_relations:
        llm_by_ids[(rel.src_id, rel.dst_id)] = rel

    result: list[Relation] = []
    for sr in static_relations:
        label = find_llm_label(sr.src_cluster_id, sr.dst_cluster_id, llm_by_ids)
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
    #
    # Keep only those whose BOTH endpoints resolve to a live component id.
    # ``validate_relation_component_names`` already rejects unresolved names at
    # generation time, but this set is assembled from *persisted* relations across
    # every level: the incremental path re-reads the prior root set, which can
    # still name a component deleted in a deep sub-scope. Such a relation would
    # otherwise persist as a dangling endpoint in analysis.json.
    static_keys = {(sr.src_cluster_id, sr.dst_cluster_id) for sr in static_relations}
    llm_only_seen: set[tuple[str, str]] = set()
    # Sort so that, when the same (src_id, dst_id) is declared at multiple levels
    # with different labels, the surviving label is deterministic (smallest by
    # relation) rather than dependent on sub_analyses completion order.
    sorted_llm_relations = sorted(all_llm_relations, key=lambda r: (r.src_id, r.dst_id, r.relation))
    for rel in sorted_llm_relations:
        if not (rel.src_id and rel.dst_id):
            continue
        if rel.src_id not in id_to_name or rel.dst_id not in id_to_name:
            continue
        key = (rel.src_id, rel.dst_id)
        if key in static_keys or key in llm_only_seen:
            # Already emitted (static-backed above, or a duplicate LLM pair from
            # another level); keep only the first (smallest-label) occurrence.
            continue
        # Check this isn't a parent-level relation superseded by a child-level one.
        # E.g., skip "1" -> "2" if "1.1" -> "2.3" exists in static_keys.
        is_superseded = any(
            src.startswith(rel.src_id + ".") or src == rel.src_id
            for src, dst in static_keys
            if dst.startswith(rel.dst_id + ".") or dst == rel.dst_id
        )
        if not is_superseded:
            llm_only_seen.add(key)
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

    # Stable order: the static portion is already sorted, but the LLM-only tail
    # is appended in sub_analyses completion order (nondeterministic across runs).
    # Sort the whole set so the serialized analysis.json and any rolled-up label
    # choice are reproducible.
    result.sort(key=lambda r: (r.src_id, r.dst_id, r.relation))

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
    - LLM only with explicit evidence: keep with is_static=False
    - LLM only without evidence: drop
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
                    evidence=llm_rel.evidence,
                    src_id=src_id,
                    dst_id=dst_id,
                    edge_count=static_rel.edge_count,
                    is_static=True,
                    bridge_edges=static_rel.bridge_edges,
                )
            )
            matched_static_keys.add((static_rel.src_cluster_id, static_rel.dst_cluster_id))
        else:
            if not llm_rel.evidence.strip():
                continue
            merged.append(
                Relation(
                    relation=llm_rel.relation,
                    src_name=llm_rel.src_name,
                    dst_name=llm_rel.dst_name,
                    evidence=llm_rel.evidence,
                    src_id=src_id,
                    dst_id=dst_id,
                    edge_count=0,
                    is_static=False,
                    bridge_edges=[],
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
                    evidence="",
                    src_id=src_id,
                    dst_id=dst_id,
                    edge_count=sr.edge_count,
                    is_static=True,
                    bridge_edges=sr.bridge_edges,
                )
            )

    logger.info(
        f"Merged relations: {len(merged)} total "
        f"({len(matched_static_keys)} LLM+static, "
        f"{len(merged) - len(matched_static_keys) - (len(llm_relations) - len(matched_static_keys))} static-only, "
        f"{len(llm_relations) - len(matched_static_keys)} LLM-only)"
    )
    return merged
