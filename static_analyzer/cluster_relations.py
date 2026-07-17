"""
Static-analysis-based inter-component relationship building.

This module builds relationships between components from actual CFG (Call Flow Graph)
edges — no LLM needed.
"""

import logging
from collections import defaultdict
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

from constants import DEFAULT_STATIC_RELATION_LABEL
from agents.analysis_result_responses import AnalysisInsights, Relation, RelationEdge
from agents.relation_edges import append_or_merge_relation
from static_analyzer.program_graph import ProgramEdgeKind, ProgramGraph, ProgramNodeKind, file_node_id

logger = logging.getLogger(__name__)


@dataclass
class ClusterRelation:
    """A relationship between two components derived from static CFG analysis."""

    src_cluster_id: str  # component's component_id, e.g. "1.2"
    dst_cluster_id: str  # e.g. "3"
    all_edges: list[RelationEdge] = field(default_factory=list)


@dataclass(frozen=True, order=True)
class ArchitecturalRelationEvidence:
    kind: ProgramEdgeKind
    source: str
    target: str
    details: tuple[str, ...] = ()


@dataclass
class ComponentRelationCandidate:
    src_cluster_id: str
    dst_cluster_id: str
    evidence: list[ArchitecturalRelationEvidence] = field(default_factory=list)


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


def build_global_node_to_component_map(
    root_analysis: AnalysisInsights,
    sub_analyses: dict[str, AnalysisInsights],
) -> dict[str, str]:
    """Map each node to the deepest currently expanded component that owns it."""
    node_to_component = build_node_to_component_map(root_analysis)
    for parent_id, sub_analysis in sorted(sub_analyses.items(), key=lambda item: (item[0].count("."), item[0])):
        for node_name, component_id in build_node_to_component_map(sub_analysis).items():
            if component_id.startswith(f"{parent_id}."):
                node_to_component[node_name] = component_id
    return node_to_component


def build_component_relations(
    node_to_component: dict[str, str],
    cfg_graphs: dict[str, ProgramGraph],
) -> list[ClusterRelation]:
    """Build inter-component relations from actual CFG edges.

    For every CFG edge where src and dst belong to different components,
    count and collect the concrete bridge methods.

    Args:
        node_to_component: Mapping from node qualified_name to component_id.
        cfg_graphs: Mapping from language to ProgramGraph.

    Returns:
        List of ClusterRelation objects, one per (src_component, dst_component) pair.
    """
    edge_pairs: dict[tuple[str, str], list[RelationEdge]] = defaultdict(list)
    for cfg in cfg_graphs.values():
        for edge in cfg.call_edges():
            src_name = edge.source
            dst_name = edge.target
            src_comp = node_to_component.get(src_name)
            dst_comp = node_to_component.get(dst_name)
            if src_comp and dst_comp and src_comp != dst_comp:
                key = (src_comp, dst_comp)
                edge_pairs[key].append(RelationEdge.from_program_edge(edge, cfg))

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


def build_component_relation_candidates(
    node_to_component: dict[str, str],
    program_graphs: dict[str, ProgramGraph],
) -> list[ComponentRelationCandidate]:
    """Build component-pair candidates from calls, imports, and inheritance."""
    evidence_by_pair: dict[tuple[str, str], set[ArchitecturalRelationEvidence]] = defaultdict(set)
    for graph in program_graphs.values():
        file_owners, file_packages, package_owners = _structural_owners(graph, node_to_component)
        for edge in graph.edges:
            if edge.kind in {ProgramEdgeKind.CALL, ProgramEdgeKind.INHERITS}:
                source_owners = _symbol_owners(graph, edge.source, node_to_component)
                target_owners = _symbol_owners(graph, edge.target, node_to_component)
                details: tuple[str, ...] = ()
            elif edge.kind == ProgramEdgeKind.IMPORTS:
                source_owners = file_owners.get(edge.source, set())
                target_owners = _import_target_owners(
                    graph,
                    edge.target,
                    edge.metadata,
                    node_to_component,
                    file_owners,
                    file_packages,
                    package_owners,
                )
                modules = set(edge.metadata.get("declared_modules", []))
                module = edge.metadata.get("declared_module")
                if module:
                    modules.add(str(module))
                imported_names = {str(name) for name in edge.metadata.get("imported_names", [])}
                details = tuple(sorted({*modules, *imported_names}))
            else:
                continue

            evidence = ArchitecturalRelationEvidence(edge.kind, edge.source, edge.target, details)
            for source_owner in source_owners:
                for target_owner in target_owners:
                    if source_owner != target_owner:
                        evidence_by_pair[(source_owner, target_owner)].add(evidence)

    return [
        ComponentRelationCandidate(source, target, sorted(evidence))
        for (source, target), evidence in sorted(evidence_by_pair.items())
    ]


def render_component_relation_candidates(
    candidates: list[ComponentRelationCandidate],
    component_names: dict[str, str],
) -> str:
    """Render deterministic architectural evidence for agent context."""
    lines: list[str] = []
    for candidate in candidates:
        source = component_names.get(candidate.src_cluster_id, candidate.src_cluster_id)
        target = component_names.get(candidate.dst_cluster_id, candidate.dst_cluster_id)
        count = len(candidate.evidence)
        lines.append(
            f"{source} [{candidate.src_cluster_id}] -> {target} [{candidate.dst_cluster_id}] "
            f"({count} evidence item{'s' if count != 1 else ''}):"
        )
        for evidence in candidate.evidence[:10]:
            if evidence.kind == ProgramEdgeKind.IMPORTS:
                details = ", ".join(evidence.details) or evidence.target
                lines.append(f"  IMPORTS: {details}")
            else:
                lines.append(f"  {evidence.kind.value.upper()}: {evidence.source} -> {evidence.target}")
        if len(candidate.evidence) > 10:
            lines.append(f"  ... and {len(candidate.evidence) - 10} more")
    return "\n".join(lines)


def _symbol_owners(graph: ProgramGraph, symbol_id: str, node_to_component: dict[str, str]) -> set[str]:
    canonical_id = graph.resolve_symbol_id(symbol_id)
    owner = node_to_component.get(canonical_id)
    return {owner} if owner else set()


def _structural_owners(
    graph: ProgramGraph,
    node_to_component: dict[str, str],
) -> tuple[dict[str, set[str]], dict[str, str], dict[str, set[str]]]:
    file_owners: dict[str, set[str]] = defaultdict(set)
    for symbol_id, owner in node_to_component.items():
        node = graph.nodes.get(graph.resolve_symbol_id(symbol_id))
        if node is None or node.kind != ProgramNodeKind.SYMBOL or not node.file_path:
            continue
        file_owners[file_node_id(node.file_path)].add(owner)

    file_packages: dict[str, str] = {}
    for edge in graph.edges_of_kind(ProgramEdgeKind.CONTAINS):
        source = graph.nodes[edge.source]
        target = graph.nodes[edge.target]
        if source.kind == ProgramNodeKind.PACKAGE and target.kind == ProgramNodeKind.FILE:
            file_packages[edge.target] = edge.source

    package_owners: dict[str, set[str]] = defaultdict(set)
    for file_id, owners in file_owners.items():
        package_id = file_packages.get(file_id)
        if package_id:
            package_owners[package_id].update(owners)
    return dict(file_owners), file_packages, dict(package_owners)


def _import_target_owners(
    graph: ProgramGraph,
    target_id: str,
    metadata: dict[str, Any],
    node_to_component: dict[str, str],
    file_owners: dict[str, set[str]],
    file_packages: dict[str, str],
    package_owners: dict[str, set[str]],
) -> set[str]:
    target = graph.nodes[target_id]
    if target.kind != ProgramNodeKind.FILE:
        return set()

    imported_names = {str(name) for name in metadata.get("imported_names", []) if name != "*"}
    target_package = file_packages.get(target_id)
    if imported_names:
        owners = {
            owner
            for symbol_id, owner in node_to_component.items()
            if (node := graph.nodes.get(graph.resolve_symbol_id(symbol_id))) is not None
            and node.kind == ProgramNodeKind.SYMBOL
            and node.name in imported_names
            and file_packages.get(file_node_id(node.file_path)) == target_package
        }
        return owners

    direct_owners = file_owners.get(target_id, set())
    if direct_owners:
        return direct_owners
    return package_owners.get(target_package, set()) if target_package else set()


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
    cfg_graphs: dict[str, ProgramGraph],
) -> list[Relation]:
    """Build deterministic project-wide relations at the current expansion frontier."""
    node_to_component = build_global_node_to_component_map(root_analysis, sub_analyses)
    static_relations = build_component_relations(node_to_component, cfg_graphs)
    id_to_name = _collect_component_names(root_analysis, sub_analyses)
    live_ids = set(id_to_name)
    llm_relations = [
        relation
        for relation in _collect_authoritative_relations(root_analysis, sub_analyses)
        if relation.src_id != relation.dst_id
    ]

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
            key_edges, all_edges = Relation._merge_edges(inherited_key_edges, static_rel.all_edges)
            relation = Relation(
                relation=llm_relation.relation,
                src_name=id_to_name.get(src_id, src_id),
                dst_name=id_to_name.get(dst_id, dst_id),
                evidence=llm_relation.evidence,
                evidence_references=llm_relation.evidence_references,
                key_edges=key_edges,
                src_id=src_id,
                dst_id=dst_id,
                is_static=True,
                all_edges=all_edges,
            )
        global_relations[(src_id, dst_id)] = relation

    for llm_rel in llm_relations:
        pair = (llm_rel.src_id, llm_rel.dst_id)
        if llm_rel.src_id not in live_ids or llm_rel.dst_id not in live_ids:
            continue
        if pair in static_pairs or pair in superseded_llm_pairs:
            continue
        global_relations[pair] = llm_rel.with_merged_edges()

    return sorted(global_relations.values(), key=lambda rel: (rel.src_id, rel.dst_id))


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
        if src_id == dst_id:
            continue

        # Match static relation in the same direction only
        static_rel = static_by_ids.get((src_id, dst_id))
        static_edges = static_rel.all_edges if static_rel else []
        has_evidence = bool(llm_rel.evidence.strip())

        if not static_edges and not llm_rel.key_edges and not has_evidence:
            continue
        if not static_edges and not llm_rel.key_edges and has_evidence:
            logger.warning(
                "Keeping LLM-only relation without static or key-edge backing: %s -> %s (%s). Evidence: %s",
                llm_rel.src_name,
                llm_rel.dst_name,
                llm_rel.relation,
                llm_rel.evidence,
            )

        key_edges, all_edges = Relation._merge_edges(llm_rel.key_edges, static_edges)
        for edge in static_edges:
            matched_static_edge_ids.add((src_id, dst_id, edge.identity()))
        append_or_merge_relation(
            merged,
            Relation(
                relation=llm_rel.relation,
                src_name=llm_rel.src_name,
                dst_name=llm_rel.dst_name,
                evidence=llm_rel.evidence,
                evidence_references=llm_rel.evidence_references,
                key_edges=key_edges,
                src_id=src_id,
                dst_id=dst_id,
                is_static=bool(static_edges),
                all_edges=all_edges,
            ),
        )

    for static_rel in static_relations:
        src_name = id_to_name.get(static_rel.src_cluster_id, static_rel.src_cluster_id)
        dst_name = id_to_name.get(static_rel.dst_cluster_id, static_rel.dst_cluster_id)
        unmatched_edges = [
            edge
            for edge in static_rel.all_edges
            if (static_rel.src_cluster_id, static_rel.dst_cluster_id, edge.identity()) not in matched_static_edge_ids
        ]
        if unmatched_edges:
            append_or_merge_relation(
                merged,
                Relation.from_edges(
                    DEFAULT_STATIC_RELATION_LABEL,
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
