"""Enrichment of the LLM-generated analysis with static-analysis-derived facts.

The clustering stage fixes the component groups; the LLM only names and
describes them. ``StaticAnalysisEnricher`` reconciles the LLM output with one
scope's ``ClusteringResults`` — one component per group, every CFG method
assigned to exactly one component, and the static relations between them.

The module-level functions serve the incremental path, whose scoped inputs
(cluster results, subgraphs, id prefixes) are assembled per operation rather
than carried by a ``ClusteringResults``.
"""

import logging
from collections import defaultdict
from pathlib import Path

import networkx as nx

from agents.agent_responses import (
    AnalysisInsights,
    ClusterAnalysis,
    ClustersGroup,
    Component,
    ComponentArchitecture,
)
from agents.cluster_ids import CodeBoardingClusterId, CodeBoardingClusterIds, GraphClusterId
from agents.content_hash import (
    SourceCache,
    hash_method_body,
    read_source_lines,
)
from agents.file_index_models import FileMethodGroup, MethodEntry
from diagram_analysis.file_index import build_files_index
from repo_utils.path_utils import normalize_repo_path
from static_analyzer.analysis_result import StaticAnalysisResults
from static_analyzer.clustering.cluster_helpers import group_symbols
from static_analyzer.clustering.cluster_relations import (
    build_component_relations,
    build_node_to_component_map,
    merge_relations,
)
from static_analyzer.clustering.models import ClusterResult
from static_analyzer.clustering.models import ClusteringResults
from static_analyzer.constants import CALLABLE_TYPES, CLASS_TYPES, Language
from static_analyzer.graph import CallGraph
from static_analyzer.node import Node

logger = logging.getLogger(__name__)


class StaticAnalysisEnricher:
    """Infills deterministic data into one scope's LLM-generated analysis.

    Bound to the scope's ``ClusteringResults``: the project scope for the
    abstraction flow, a component's subgraph scope for the details flow.
    """

    def __init__(self, clustering: ClusteringResults, repo_dir: Path):
        self.clustering = clustering
        self.repo_dir = repo_dir

    def pin_components_to_groups(self, architecture: ComponentArchitecture) -> None:
        """Force exactly one component per fixed group — the count is Leiden's, not the LLM's.

        The groups (and their membership) are decided deterministically upstream;
        the LLM only names and describes them. Whatever the LLM returns, we pin the
        result to one component per group: the LLM's component that claimed a group
        keeps its name/description/key_entities; any group the LLM merged away or
        dropped gets a deterministic fallback so the count never drifts.
        """
        node_lookup = self.clustering.combined().clusters
        src_group_to_component: dict[str, Component] = {}
        for comp in architecture.components:
            for group_name in comp.source_group_names:
                src_group_to_component.setdefault(group_name.lower(), comp)

        used: set[int] = set()
        final: list[Component] = []
        for group in self.clustering.cluster_analysis.cluster_groups:
            comp = src_group_to_component.get(group.name.lower())
            if comp is None or id(comp) in used:
                comp = _fallback_component(group, node_lookup)
            else:
                used.add(id(comp))
                comp = comp.model_copy(deep=True)
            comp.source_group_names = [group.name]
            final.append(comp)

        if len(final) != len(architecture.components):
            logger.info(
                f"[Enrichment] Reconciled {len(architecture.components)} LLM components "
                f"to {len(final)} (one per deterministic group)"
            )
        architecture.components = final

    def resolve_cluster_ids(self, analysis: AnalysisInsights) -> None:
        """Resolve source_cluster_ids deterministically from source_group_names via case-insensitive lookup."""
        group_name_to_ids: dict[str, list[GraphClusterId]] = {
            cc.name.lower(): cc.cluster_ids for cc in self.clustering.cluster_analysis.cluster_groups
        }

        for component in analysis.components:
            resolved_ids = [
                cid for gname in component.source_group_names for cid in group_name_to_ids.get(gname.lower(), [])
            ]
            unresolved = [g for g in component.source_group_names if g.lower() not in group_name_to_ids]
            for gname in unresolved:
                logger.warning(f"[Enrichment] Unresolved group name '{gname}' for component '{component.name}'")
            component.source_cluster_ids = CodeBoardingClusterIds.from_graph_ids(set(resolved_ids))

    def populate_file_methods(self, analysis: AnalysisInsights) -> None:
        """Deterministically populate ``file_methods`` on every component.

        Node-centric approach guaranteeing 100% coverage:
        1. Build cluster_id -> component mapping from source_cluster_ids.
        2. Validate that all clusters are mapped (log error if not).
        3. For each node, assign via its cluster -> component mapping.
        4. Orphan nodes (not in any cluster) go to the nearest cluster's component
           or fall back to the first component.
        5. Build ``FileMethodGroup`` lists grouped by file path.

        Runs before ``build_static_relations`` prefixes the scope's cluster ids,
        so the components still carry unqualified ids here.
        """
        # NOTE: These maps are intentionally rebuilt on each call — not cached — because
        # cluster_results differ per scope (full graph at the top level vs.
        # per-component subgraph in the details flow, which runs in parallel).
        component_nodes, total_nodes = _assign_component_nodes(
            analysis, self.clustering.cluster_results, self.clustering.static_analysis, self.clustering.cfg_graphs, ""
        )

        # One cache shared across the per-component method build and the files
        # index so each source file is read from disk once, not twice.
        source_cache: SourceCache = {}
        for comp in analysis.components:
            comp.file_methods = _build_file_methods_from_nodes(
                component_nodes.get(comp.component_id, []), self.repo_dir, source_cache
            )

        analysis.files = build_files_index(analysis, self.repo_dir, source_cache)

        _log_node_coverage(analysis, total_nodes)

    def build_static_relations(self, analysis: AnalysisInsights) -> None:
        """Ground the scope's relations in CFG edges, prefixing cluster ids with the scope id."""
        build_static_relations(
            analysis,
            self.clustering.static_analysis,
            self.clustering.cfg_graphs,
            self.clustering.scope_id,
        )

    def cfg_evidence(self, analysis: AnalysisInsights) -> str:
        """Cross-component static call evidence rendered for the LLM prompts."""
        return build_scope_cfg_string(analysis, self.clustering.static_analysis)


def _fallback_component(group: ClustersGroup, node_lookup: dict[int, set[str]]) -> Component:
    """Deterministic component for a group the LLM failed to name (merged/dropped it)."""
    symbols = group_symbols(group.cluster_ids, node_lookup)
    name = symbols[0].split(".")[-1] if symbols else group.name
    return Component(name=name, description=group.description, key_entities=[])


def _scoped_cfg(
    lang: str,
    static_analysis: StaticAnalysisResults,
    cfg_graphs: dict[str, CallGraph],
) -> CallGraph:
    """The scoped CallGraph for *lang*, falling back to the global CFG."""
    if cfg_graphs and lang in cfg_graphs:
        return cfg_graphs[lang]
    return static_analysis.get_cfg(Language(lang))


def _collect_all_cfg_nodes(
    cluster_results: dict[str, ClusterResult],
    static_analysis: StaticAnalysisResults,
    cfg_graphs: dict[str, CallGraph],
) -> dict[str, Node]:
    """Build a lookup of qualified_name -> Node for all languages present in cluster_results.

    When ``cfg_graphs`` provides a scoped CallGraph (e.g. a component subgraph),
    only its nodes are included, preventing scope leakage.
    """
    all_nodes: dict[str, Node] = {}
    for lang in cluster_results:
        all_nodes.update(_scoped_cfg(lang, static_analysis, cfg_graphs).nodes)
    return all_nodes


def _build_undirected_graphs(
    cluster_results: dict[str, ClusterResult],
    static_analysis: StaticAnalysisResults,
    cfg_graphs: dict[str, CallGraph],
) -> dict[str, nx.Graph]:
    """Pre-build undirected networkx graphs for each language in cluster_results.

    Meant to be called once before iterating over orphan nodes, so that
    ``_find_nearest_cluster`` doesn't rebuild the graph on every call.
    """
    return {
        lang: _scoped_cfg(lang, static_analysis, cfg_graphs).to_networkx().to_undirected() for lang in cluster_results
    }


def _find_nearest_cluster(
    node_name: str,
    cluster_results: dict[str, ClusterResult],
    undirected_graphs: dict[str, nx.Graph],
) -> int | None:
    """Find the cluster whose members are closest to *node_name* in the call graph.

    Uses undirected shortest-path distance so that both callers and callees
    are considered.  Returns the cluster_id of the nearest cluster, or None
    if the node is completely disconnected.
    """
    best_cluster: int | None = None
    best_dist = float("inf")

    for lang, cr in cluster_results.items():
        nx_graph = undirected_graphs.get(lang)
        if nx_graph is None or node_name not in nx_graph:
            continue

        try:
            distances = nx.single_source_shortest_path_length(nx_graph, node_name)
        except nx.NetworkXError:
            continue

        for cluster_id, members in cr.clusters.items():
            for member in members:
                d = distances.get(member)
                if d is not None and d < best_dist:
                    best_dist = d
                    best_cluster = cluster_id

    return best_cluster


def _build_file_methods_from_nodes(
    nodes: list[Node],
    repo_dir: Path,
    source_cache: SourceCache | None = None,
) -> list[FileMethodGroup]:
    """Group a flat list of Nodes into FileMethodGroups sorted by file then line.

    Only includes methods, functions, and classes/interfaces — variables,
    constants, properties, and fields are excluded. Pass ``source_cache`` to
    reuse file reads across a whole ``populate_file_methods`` pass.
    """
    allowed_types = CALLABLE_TYPES | CLASS_TYPES
    by_file: dict[str, dict[tuple[int, int, str, str], MethodEntry]] = defaultdict(dict)
    if source_cache is None:
        source_cache = {}

    def _is_more_specific(candidate: str, current: str) -> bool:
        """Prefer the most specific qualified name for the same symbol span.

        Example: keep ``module.Class.method`` over ``module.method`` when both
        point to the same file range and symbol kind.
        """
        candidate_parts = candidate.split(".")
        current_parts = current.split(".")
        if candidate_parts[-1] == current_parts[-1]:
            return len(candidate_parts) > len(current_parts)
        return len(candidate) > len(current)

    for node in nodes:
        if node.type not in allowed_types:
            continue

        rel_path = normalize_repo_path(node.file_path, repo_dir)

        method_name = node.fully_qualified_name.split(".")[-1]
        dedupe_key = (node.line_start, node.line_end, node.type.name, method_name)
        candidate = MethodEntry(
            qualified_name=node.fully_qualified_name,
            start_line=node.line_start,
            end_line=node.line_end,
            node_type=node.type.name,
            content_hash=hash_method_body(
                read_source_lines(repo_dir, rel_path, source_cache),
                node.line_start,
                node.line_end,
            ),
        )

        existing = by_file[rel_path].get(dedupe_key)
        if existing is None or _is_more_specific(candidate.qualified_name, existing.qualified_name):
            by_file[rel_path][dedupe_key] = candidate

    groups: list[FileMethodGroup] = []
    for file_path in sorted(by_file):
        methods = sorted(by_file[file_path].values(), key=lambda m: (m.start_line, m.end_line, m.qualified_name))
        groups.append(FileMethodGroup(file_path=file_path, methods=methods))
    return groups


def _build_cluster_to_component_map(analysis: AnalysisInsights) -> dict[CodeBoardingClusterId, Component]:
    """Build cluster_id -> Component mapping from source_cluster_ids."""
    cluster_to_component: dict[CodeBoardingClusterId, Component] = {}
    for comp in analysis.components:
        for cid in comp.source_cluster_ids:
            cluster_to_component[cid] = comp
    return cluster_to_component


def _build_node_to_cluster_map(
    cluster_results: dict[str, ClusterResult], source_cluster_id_prefix: str = ""
) -> tuple[dict[str, CodeBoardingClusterId], set[CodeBoardingClusterId]]:
    """Build node_name (qualified name) -> cluster_id mapping and collect all cluster IDs."""
    all_cluster_ids: set[CodeBoardingClusterId] = set()
    node_to_cluster: dict[str, CodeBoardingClusterId] = {}
    for cr in cluster_results.values():
        for cid, members in cr.clusters.items():
            cluster_id = CodeBoardingClusterIds.qualify_local_id(
                CodeBoardingClusterIds.from_graph_id(cid), source_cluster_id_prefix
            )
            all_cluster_ids.add(cluster_id)
            for name in members:
                node_to_cluster[name] = cluster_id
    return node_to_cluster, all_cluster_ids


def _validate_cluster_coverage(
    cluster_to_component: dict[CodeBoardingClusterId, Component], all_cluster_ids: set[CodeBoardingClusterId]
) -> None:
    """Log an error if any cluster IDs are not mapped to a component."""
    unmapped_cluster_ids = sorted(all_cluster_ids - set(cluster_to_component.keys()))
    if unmapped_cluster_ids:
        logger.error(
            f"{len(unmapped_cluster_ids)}/{len(all_cluster_ids)} clusters not mapped "
            f"via source_cluster_ids: {unmapped_cluster_ids}. This should never happen — all clusters must be "
            f"assigned to components by the LLM."
        )


def _find_component_by_file(
    node: Node,
    cluster_results: dict[str, ClusterResult],
    cluster_to_component: dict[str, Component],
    source_cluster_id_prefix: str = "",
) -> Component | None:
    """Try to assign a node to a component based on its file already belonging to a cluster."""
    file_path = node.file_path
    if not file_path:
        return None
    for cr in cluster_results.values():
        cluster_ids = cr.get_clusters_for_file(file_path)
        for cid in cluster_ids:
            cluster_id = CodeBoardingClusterIds.qualify_local_id(
                CodeBoardingClusterIds.from_graph_id(cid), source_cluster_id_prefix
            )
            comp = cluster_to_component.get(cluster_id)
            if comp is not None:
                return comp
    return None


def _assign_nodes_to_components(
    all_nodes: dict[str, Node],
    node_to_cluster: dict[str, str],
    cluster_to_component: dict[str, Component],
    cluster_results: dict[str, ClusterResult],
    fallback_component: Component,
    static_analysis: StaticAnalysisResults,
    cfg_graphs: dict[str, CallGraph],
    source_cluster_id_prefix: str = "",
) -> dict[str, list[Node]]:
    """Assign every node to a component via its cluster, file co-location, graph distance, or fallback."""
    component_nodes: dict[str, list[Node]] = defaultdict(list)
    unassigned: list[str] = []

    for qname, node in all_nodes.items():
        cid = node_to_cluster.get(qname)
        if cid is not None and cid in cluster_to_component:
            component_nodes[cluster_to_component[cid].component_id].append(node)
        else:
            unassigned.append(qname)

    if unassigned:
        logger.info(f"Assigning {len(unassigned)} orphan node(s)")

    assigned_by_file = 0
    assigned_by_graph = 0
    assigned_by_fallback = 0
    fallback_files: set[str] = set()

    # Pre-build undirected graphs once for all orphan lookups
    undirected_graphs = _build_undirected_graphs(cluster_results, static_analysis, cfg_graphs) if unassigned else {}

    for qname in unassigned:
        node = all_nodes[qname]

        # 1. Try file co-location: if the node's file already belongs to a cluster/component
        comp = _find_component_by_file(node, cluster_results, cluster_to_component, source_cluster_id_prefix)
        if comp is not None:
            assigned_by_file += 1
            component_nodes[comp.component_id].append(node)
            continue

        # 2. Try graph distance: find the nearest cluster in the call graph
        nearest_cid = _find_nearest_cluster(qname, cluster_results, undirected_graphs)
        nearest_cluster_id = (
            CodeBoardingClusterIds.qualify_local_id(
                CodeBoardingClusterIds.from_graph_id(nearest_cid), source_cluster_id_prefix
            )
            if nearest_cid is not None
            else ""
        )
        if nearest_cluster_id in cluster_to_component:
            comp = cluster_to_component[nearest_cluster_id]
            assigned_by_graph += 1
            component_nodes[comp.component_id].append(node)
            continue

        # 3. Last resort: fallback component
        assigned_by_fallback += 1
        fallback_files.add(node.file_path)
        component_nodes[fallback_component.component_id].append(node)

    if unassigned:
        logger.info(
            f"Orphan assignment: {assigned_by_file} by file, "
            f"{assigned_by_graph} by graph distance, {assigned_by_fallback} to fallback"
        )
    if assigned_by_fallback:
        logger.error(
            f"{assigned_by_fallback} node(s) fell back to '{fallback_component.name}' "
            f"— files: {sorted(fallback_files)}"
        )

    return component_nodes


def _log_node_coverage(analysis: AnalysisInsights, total_nodes: int) -> None:
    """Log the percentage of nodes assigned to components."""
    assigned_nodes = sum(len(fg.methods) for comp in analysis.components for fg in comp.file_methods)
    pct = (assigned_nodes / total_nodes * 100) if total_nodes else 0
    logger.info(f"Node coverage: {assigned_nodes}/{total_nodes} ({pct:.1f}%) nodes assigned to components")


def _assign_component_nodes(
    analysis: AnalysisInsights,
    cluster_results: dict[str, ClusterResult],
    static_analysis: StaticAnalysisResults,
    cfg_graphs: dict[str, CallGraph],
    source_cluster_id_prefix: str,
) -> tuple[dict[str, list[Node]], int]:
    """Map every CFG node onto a component; returns the mapping and the node total."""
    all_nodes = _collect_all_cfg_nodes(cluster_results, static_analysis, cfg_graphs)
    cluster_to_component = _build_cluster_to_component_map(analysis)
    node_to_cluster, all_cluster_ids = _build_node_to_cluster_map(cluster_results, source_cluster_id_prefix)
    _validate_cluster_coverage(cluster_to_component, all_cluster_ids)

    component_nodes = _assign_nodes_to_components(
        all_nodes,
        node_to_cluster,
        cluster_to_component,
        cluster_results,
        analysis.components[0],
        static_analysis,
        cfg_graphs,
        source_cluster_id_prefix,
    )
    return component_nodes, len(all_nodes)


def component_file_method_groups(
    analysis: AnalysisInsights,
    cluster_results: dict[str, ClusterResult],
    repo_dir: Path,
    static_analysis: StaticAnalysisResults,
    cfg_graphs: dict[str, CallGraph],
    source_cluster_id_prefix: str = "",
    source_cache: SourceCache | None = None,
) -> dict[str, list[FileMethodGroup]]:
    """Per-component ``FileMethodGroup`` lists without mutating the analysis.

    The incremental path patches these into a scope surgically instead of
    overwriting every component the way ``populate_file_methods`` does.
    """
    component_nodes, _total = _assign_component_nodes(
        analysis, cluster_results, static_analysis, cfg_graphs, source_cluster_id_prefix
    )
    if source_cache is None:
        source_cache = {}
    return {
        component_id: _build_file_methods_from_nodes(nodes, repo_dir, source_cache)
        for component_id, nodes in component_nodes.items()
    }


def build_static_relations(
    analysis: AnalysisInsights,
    static_analysis: StaticAnalysisResults,
    cfg_graphs: dict[str, CallGraph],
    source_cluster_id_prefix: str = "",
) -> None:
    """Build inter-component relations from CFG edges and merge with LLM relations.

    Static analysis supplies evidence for LLM-discovered architectural relations:
    - LLM + static match: keep LLM label and attach all matching edges.
    - LLM only with evidence/key_edges: keep as runtime or external communication.
    - Static only: keep out of user-facing relations unless the LLM selected the pair.

    ``cfg_graphs`` is the scope's graphs — pass ``static_analysis.available_cfgs()``
    for the whole project.
    """
    node_to_component = build_node_to_component_map(analysis)
    static_relations = build_component_relations(node_to_component, cfg_graphs)
    analysis.components_relations = merge_relations(analysis.components_relations, static_relations, analysis)
    _prefix_local_cluster_ids(analysis, source_cluster_id_prefix)


def _prefix_local_cluster_ids(analysis: AnalysisInsights, prefix: str) -> None:
    """Prefix detail-subgraph cluster ids with their owning component scope."""
    for component in analysis.components:
        component.source_cluster_ids = CodeBoardingClusterIds.qualify_local_ids(component.source_cluster_ids, prefix)


def build_scope_cfg_string(analysis: AnalysisInsights, static_analysis: StaticAnalysisResults) -> str:
    """Render cross-component communication edges as a human-readable string for the LLM.

    For every CFG edge where src belongs to component A and dst belongs to
    component B (A != B), this produces a grouped summary like:

        ComponentA -> ComponentB (3 edges):
          src_pkg.MethodX -> dst_pkg.MethodY
          src_pkg.MethodZ -> dst_pkg.MethodW
    """
    node_to_component = build_node_to_component_map(analysis)
    id_to_name = {c.component_id: c.name for c in analysis.components}
    cfg_graphs = static_analysis.available_cfgs()
    static_relations = build_component_relations(node_to_component, cfg_graphs)

    if not static_relations:
        return "No cross-component communication edges found."

    lines: list[str] = []
    for relation in static_relations:
        src_id = relation.src_cluster_id
        dst_id = relation.dst_cluster_id
        src_label = id_to_name.get(src_id, src_id)
        dst_label = id_to_name.get(dst_id, dst_id)
        edge_count = len(relation.all_edges)
        lines.append(f"\n{src_label} -> {dst_label} ({edge_count} edge{'s' if edge_count != 1 else ''}):")
        for edge in relation.all_edges[:10]:
            short_s = edge.source.qualified_name.split(".")[-1]
            short_d = edge.target.qualified_name.split(".")[-1]
            lines.append(f"  {short_s} -> {short_d}")
        if edge_count > 10:
            lines.append(f"  ... and {edge_count - 10} more")

    return "\n".join(lines)
