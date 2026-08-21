import logging
from collections import defaultdict
from pathlib import Path

import networkx as nx

from agents.agent_responses import (
    AnalysisInsights,
    Component,
    ComponentArchitecture,
)
from agents.file_index_models import FileMethodGroup, MethodEntry
from agents.content_hash import (
    SourceCache,
    hash_method_body,
    read_source_lines,
)
from agents.cluster_ids import CodeBoardingClusterIds
from clustering_ids import ClusterId, ComponentId
from constants import MIN_CLUSTERS_THRESHOLD
from diagram_analysis.cluster_delta import _delta_for_language
from diagram_analysis.file_index import build_files_index
from repo_utils.path_utils import normalize_repo_path
from static_analyzer.analysis_result import StaticAnalysisResults
from static_analyzer.cluster_helpers import (
    combine_cluster_results,
    group_symbols,
    reindex_across_languages,
)
from static_analyzer.cluster_relations import (
    build_component_relations,
    build_node_to_component_map,
    merge_relations,
)
from static_analyzer.config import CALLABLE_TYPES, CLASS_TYPES, Language
from static_analyzer.cfg import CallGraph, DEFAULT_REFERENCE_KINDS
from static_analyzer.clustering import (
    METHOD_LEVEL_STRATEGY,
    ClusterGroup,
    ClusterResult,
    ClusterScopeResult,
    ClusteringService,
)
from static_analyzer.node import Node

logger = logging.getLogger(__name__)


def cluster_group_ids(groups: list[ClusterGroup]) -> dict[str, list[ClusterId]]:
    return {f"Group {index}": group.cluster_ids for index, group in enumerate(groups, start=1)}


def cluster_group_descriptions(
    groups: list[ClusterGroup],
    cluster_results: dict[str, ClusterResult],
) -> dict[str, str]:
    combined = combine_cluster_results(cluster_results)
    descriptions: dict[str, str] = {}
    for name, group in zip(cluster_group_ids(groups), groups, strict=True):
        symbols = sorted(
            group_symbols(group.cluster_ids, combined.clusters),
            key=lambda qualified_name: (qualified_name.count("."), qualified_name),
        )
        files = sorted(
            {path for cluster_id in group.cluster_ids for path in combined.cluster_to_files.get(cluster_id, set())}
        )
        parts = [f"{len(group.cluster_ids)} leaf clusters, {len(symbols)} symbols across {len(files)} files."]
        if files:
            shown = ", ".join(Path(path).name for path in files[:8])
            parts.append(f"Files: {shown}{', ...' if len(files) > 8 else ''}")
        if symbols:
            shown = ", ".join(symbols[:12])
            parts.append(f"Key symbols: {shown}{', ...' if len(symbols) > 12 else ''}")
        descriptions[name] = " ".join(parts)
    return descriptions


def render_cluster_groups(group_ids: dict[str, list[ClusterId]], group_descriptions: dict[str, str]) -> str:
    if not group_ids:
        return "No clusters analyzed."
    body = "\n".join(
        f"**{name}** (cluster_ids: [{', '.join(str(cluster_id) for cluster_id in cluster_ids)}])\n"
        f"   {group_descriptions[name]}"
        for name, cluster_ids in group_ids.items()
    )
    return f"# Grouped Cluster Components\n{body}"


def _fallback_component(
    group_name: str,
    cluster_ids: list[int],
    description: str,
    node_lookup: dict[int, set[str]],
) -> Component:
    """Deterministic component for a group the LLM failed to name (merged/dropped it)."""
    symbols = group_symbols(cluster_ids, node_lookup)
    name = symbols[0].split(".")[-1] if symbols else group_name
    return Component(name=name, description=description, key_entities=[])


class ClusterMethodsMixin:
    """Shared cluster handling for the abstraction and details agents.

    Partitions leaf clusters into component groups, assigns every CFG method to
    exactly one component, and derives the static relations between them. All
    methods are stateless with respect to ``ClusterResult`` — cluster results are
    always passed in explicitly.
    """

    # These attributes must be provided by the class using this mixin
    repo_dir: Path
    static_analysis: StaticAnalysisResults

    @staticmethod
    def assemble_one_component_per_group(
        architecture: ComponentArchitecture,
        scope: ClusterScopeResult,
    ) -> None:
        """Force exactly one component per fixed group — the count is Leiden's, not the LLM's.

        The groups (and their membership) are decided deterministically upstream;
        the LLM only names and describes them. Whatever the LLM returns, we pin the
        result to one component per group: the LLM's component that claimed a group
        keeps its name/description/key_entities; any group the LLM merged away or
        dropped gets a deterministic fallback so the count never drifts.
        """
        node_lookup = combine_cluster_results(scope.leaf_clusters_by_language).clusters
        group_ids = cluster_group_ids(scope.groups)
        descriptions = cluster_group_descriptions(scope.groups, scope.leaf_clusters_by_language)
        claimant: dict[str, Component] = {}
        for comp in architecture.components:
            for group_name in comp.source_group_names:
                claimant.setdefault(group_name.lower(), comp)

        used: set[int] = set()
        final: list[Component] = []
        for group_name, group in zip(group_ids, scope.groups, strict=True):
            cluster_ids = group_ids[group_name]
            comp = claimant.get(group_name.lower())
            if comp is None or id(comp) in used:
                comp = _fallback_component(
                    group_name,
                    cluster_ids,
                    descriptions[group_name],
                    node_lookup,
                )
            else:
                used.add(id(comp))
                comp = comp.model_copy(deep=True)
            comp.source_group_names = [group_name]
            comp.component_id = group.group_id
            final.append(comp)

        if len(final) != len(architecture.components):
            logger.info(
                f"[ClusterMethods] Reconciled {len(architecture.components)} LLM components "
                f"to {len(final)} (one per deterministic group)"
            )
        architecture.components = final

    def _ensure_unique_key_entities(self, analysis: AnalysisInsights):
        """
        Ensure that key_entities are unique across components.

        If a key_entity (identified by qualified_name) appears in multiple components,
        keep it only in the component where it's most relevant:
        1. If it's in the component's file_methods -> keep it there (highest priority)
        2. Otherwise, keep it in the first component that references it

        This prevents confusion in documentation where the same class/method
        is listed as a "key entity" for multiple components.
        """
        logger.info("Ensuring key_entities are unique across components")

        seen_entities: dict[str, Component] = {}

        for component in analysis.components:
            entities_to_remove = []

            for key_entity in component.key_entities:
                qname = key_entity.qualified_name

                if qname in seen_entities:
                    original_component = seen_entities[qname]
                    ref_file = key_entity.reference_file

                    component_files = component.file_paths()
                    original_files = original_component.file_paths()
                    current_has_file = ref_file and any(ref_file in f for f in component_files)
                    original_has_file = ref_file and any(ref_file in f for f in original_files)

                    if current_has_file and not original_has_file:
                        # Move to current component
                        original_component.key_entities = [
                            e for e in original_component.key_entities if e.qualified_name != qname
                        ]
                        seen_entities[qname] = component
                        logger.debug(f"Moved key_entity '{qname}' from {original_component.name} to {component.name}")
                    else:
                        # Keep in original component
                        entities_to_remove.append(key_entity)
                        logger.debug(
                            f"Removed duplicate key_entity '{qname}' from {component.name} (kept in {original_component.name})"
                        )
                else:
                    seen_entities[qname] = component

            component.key_entities = [e for e in component.key_entities if e not in entities_to_remove]

    def _resolve_cluster_ids_from_groups(self, analysis: AnalysisInsights, scope: ClusterScopeResult) -> None:
        """Resolve source_cluster_ids deterministically from source_group_names via case-insensitive lookup."""
        group_name_to_ids: dict[str, list[ClusterId]] = {
            name.lower(): cluster_ids for name, cluster_ids in cluster_group_ids(scope.groups).items()
        }

        for component in analysis.components:
            resolved_ids = [
                cid for gname in component.source_group_names for cid in group_name_to_ids.get(gname.lower(), [])
            ]
            unresolved = [g for g in component.source_group_names if g.lower() not in group_name_to_ids]
            for gname in unresolved:
                logger.warning(
                    f"[{self.__class__.__name__}] Unresolved group name '{gname}' for component '{component.name}'"
                )
            component.source_cluster_ids = CodeBoardingClusterIds.from_graph_ids(set(resolved_ids))

    def _expand_to_method_level_clusters(self, cfg: CallGraph, cluster_result: ClusterResult) -> ClusterResult:
        """
        Expand cluster results to method-level granularity when there are too few clusters.

        When a subgraph has fewer than MIN_CLUSTERS_THRESHOLD clusters, this creates
        synthetic clusters where each method/function becomes its own cluster. This
        ensures fine-grained method assignment even for small components.

        Args:
            cfg: The CallGraph containing nodes to cluster
            cluster_result: Original cluster result (may have insufficient clusters)

        Returns:
            New ClusterResult with method-level clusters (each method = 1 cluster)
        """
        num_clusters = len(cluster_result.clusters)

        if num_clusters >= MIN_CLUSTERS_THRESHOLD:
            return cluster_result

        logger.info(f"Expanding to method-level clusters: {num_clusters} clusters < {MIN_CLUSTERS_THRESHOLD} threshold")

        # Create synthetic clusters: each callable node becomes its own cluster
        new_clusters: dict[int, set[str]] = {}
        new_cluster_to_files: dict[int, set[str]] = {}
        new_file_to_clusters: dict[str, set[int]] = defaultdict(set)

        cluster_id = 0
        for qname, node in sorted(cfg.nodes.items()):
            # Only create clusters for callable types (functions, methods)
            if node.type not in CALLABLE_TYPES:
                continue

            new_clusters[cluster_id] = {qname}
            new_cluster_to_files[cluster_id] = {node.file_path}
            new_file_to_clusters[node.file_path].add(cluster_id)
            cluster_id += 1

        # If we still have few clusters (e.g., only classes, no methods), include classes too
        if len(new_clusters) < MIN_CLUSTERS_THRESHOLD:
            for qname, node in sorted(cfg.nodes.items()):
                if node.type in CLASS_TYPES and qname not in {n for members in new_clusters.values() for n in members}:
                    new_clusters[cluster_id] = {qname}
                    new_cluster_to_files[cluster_id] = {node.file_path}
                    new_file_to_clusters[node.file_path].add(cluster_id)
                    cluster_id += 1

        logger.info(f"Created {len(new_clusters)} method-level clusters from {len(cfg.nodes)} nodes")

        return ClusterResult(
            clusters=new_clusters,
            cluster_to_files=new_cluster_to_files,
            file_to_clusters=dict(new_file_to_clusters),
            strategy=METHOD_LEVEL_STRATEGY,
        )

    def _create_strict_component_subgraph(
        self,
        component: Component,
        source_cluster_id_prefix: str = "",
    ) -> tuple[dict[str, ClusterResult], dict[str, CallGraph]]:
        """Cluster the subgraph spanned by exactly the component's own methods.

        Filtering by the component's qualified names (not its files) keeps a
        sibling component's methods out even when they share a file. A subgraph
        with fewer than ``MIN_CLUSTERS_THRESHOLD`` clusters is expanded to
        method-level granularity so assignment stays fine-grained.

        Returns ``(cluster_results, subgraph_cfgs)``, both keyed by language.
        Passing ``source_cluster_id_prefix`` also records the resulting cluster
        lineage on the parent CFG, so leave it empty for a read-only probe.
        """
        assigned_qnames = {method.qualified_name for group in component.file_methods for method in group.methods}
        if not assigned_qnames:
            logger.warning(f"Component {component.name} has no assigned methods")
            return {}, {}

        cluster_results: dict[str, ClusterResult] = {}
        subgraph_cfgs: dict[str, CallGraph] = {}

        for lang in self.static_analysis.get_languages():
            sub_cfg = self.static_analysis.get_cfg(lang).filter_by_nodes(assigned_qnames)
            if not sub_cfg.nodes:
                continue
            subgraph_cfgs[lang] = sub_cfg

            seeded_snapshot = ClusteringService._scoped_snapshot(
                sub_cfg,
                self.static_analysis.get_clusters(lang).method_paths,
                source_cluster_id_prefix,
            )
            if seeded_snapshot:
                sub_cluster_result = _delta_for_language(
                    str(lang), sub_cfg.to_networkx(DEFAULT_REFERENCE_KINDS), seeded_snapshot
                ).cluster_results
            else:
                sub_cluster_result = ClusteringService().cluster(sub_cfg)

            cluster_results[lang] = self._expand_to_method_level_clusters(sub_cfg, sub_cluster_result)

        reindex_across_languages(cluster_results)

        if source_cluster_id_prefix:
            for lang, cluster_result in cluster_results.items():
                self.static_analysis.get_clusters(Language(lang)).record_scope(cluster_result, source_cluster_id_prefix)

        return cluster_results, subgraph_cfgs

    def _collect_all_cfg_nodes(
        self,
        cluster_results: dict[str, ClusterResult],
        cfg_graphs: dict[str, CallGraph] | None = None,
    ) -> dict[tuple[str, str], Node]:
        """Build a language-qualified node lookup for all clustered languages.

        Args:
            cluster_results: Language -> ClusterResult mapping (used to determine languages).
            cfg_graphs: Optional scoped CallGraphs to use instead of the global CFG.
                        When provided (e.g. subgraph from DetailsAgent), only nodes
                        from these graphs are included, preventing scope leakage.
        """
        all_nodes: dict[tuple[str, str], Node] = {}
        for lang in cluster_results:
            cfg = (
                cfg_graphs[lang] if cfg_graphs and lang in cfg_graphs else self.static_analysis.get_cfg(Language(lang))
            )
            all_nodes.update({(lang, qualified_name): node for qualified_name, node in cfg.nodes.items()})
        return all_nodes

    def _build_undirected_graphs(
        self,
        cluster_results: dict[str, ClusterResult],
        cfg_graphs: dict[str, CallGraph] | None = None,
    ) -> dict[str, nx.Graph]:
        """Pre-build undirected networkx graphs for each language in cluster_results.

        Meant to be called once before iterating over orphan nodes, so that
        ``_find_nearest_cluster`` doesn't rebuild the graph on every call.

        Args:
            cluster_results: Language -> ClusterResult mapping (used to determine languages).
            cfg_graphs: Optional scoped CallGraphs to use instead of the global CFG.
        """
        graphs: dict[str, nx.Graph] = {}
        for lang in cluster_results:
            cfg = (
                cfg_graphs[lang] if cfg_graphs and lang in cfg_graphs else self.static_analysis.get_cfg(Language(lang))
            )
            graphs[lang] = cfg.to_networkx(reference_kinds=()).to_undirected()
        return graphs

    def _find_nearest_cluster(
        self,
        language: str,
        node_name: str,
        cluster_results: dict[str, ClusterResult],
        undirected_graphs: dict[str, nx.Graph],
    ) -> int | None:
        """Find the cluster whose members are closest to *node_name* in the call graph.

        Uses undirected shortest-path distance so that both callers and callees
        are considered.  Returns the cluster_id of the nearest cluster, or None
        if the node is completely disconnected.

        Args:
            node_name: Fully qualified name of the node to find the nearest cluster for.
            cluster_results: Language -> ClusterResult mapping.
            undirected_graphs: Pre-built undirected graphs (from ``_build_undirected_graphs``).
        """
        best_cluster: int | None = None
        best_dist = float("inf")

        cluster_result = cluster_results.get(language)
        nx_graph = undirected_graphs.get(language)
        if cluster_result is None or nx_graph is None or node_name not in nx_graph:
            return None

        try:
            distances = nx.single_source_shortest_path_length(nx_graph, node_name)
        except nx.NetworkXError:
            return None

        for cluster_id, members in cluster_result.clusters.items():
            for member in members:
                distance = distances.get(member)
                if distance is not None and distance < best_dist:
                    best_dist = distance
                    best_cluster = cluster_id

        return best_cluster

    def _build_file_methods_from_nodes(
        self, nodes: list[Node], source_cache: SourceCache | None = None
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

            rel_path = normalize_repo_path(node.file_path, self.repo_dir)

            method_name = node.fully_qualified_name.split(".")[-1]
            dedupe_key = (node.line_start, node.line_end, node.type.name, method_name)
            candidate = MethodEntry(
                qualified_name=node.fully_qualified_name,
                start_line=node.line_start,
                end_line=node.line_end,
                node_type=node.type.name,
                content_hash=hash_method_body(
                    read_source_lines(self.repo_dir, rel_path, source_cache),
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

    def _build_cluster_to_component_map(self, analysis: AnalysisInsights) -> dict[ComponentId, Component]:
        """Build cluster_id -> Component mapping from source_cluster_ids."""
        cluster_to_component: dict[ComponentId, Component] = {}
        for comp in analysis.components:
            for cid in comp.source_cluster_ids:
                cluster_to_component[cid] = comp
        return cluster_to_component

    def _build_node_to_cluster_map(
        self, cluster_results: dict[str, ClusterResult], source_cluster_id_prefix: str = ""
    ) -> tuple[dict[tuple[str, str], ComponentId], set[ComponentId]]:
        """Build language-qualified node-to-cluster membership and collect all cluster IDs."""
        all_cluster_ids: set[ComponentId] = set()
        node_to_cluster: dict[tuple[str, str], ComponentId] = {}
        for language, cr in cluster_results.items():
            for cid, members in cr.clusters.items():
                cluster_id = CodeBoardingClusterIds.qualify_local_id(
                    CodeBoardingClusterIds.from_graph_id(cid), source_cluster_id_prefix
                )
                all_cluster_ids.add(cluster_id)
                for name in members:
                    node_to_cluster[(language, name)] = cluster_id
        return node_to_cluster, all_cluster_ids

    def _validate_cluster_coverage(
        self, cluster_to_component: dict[ComponentId, Component], all_cluster_ids: set[ComponentId]
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
        self,
        language: str,
        node: Node,
        cluster_results: dict[str, ClusterResult],
        cluster_to_component: dict[str, Component],
        source_cluster_id_prefix: str = "",
    ) -> Component | None:
        """Try to assign a node to a component based on its file already belonging to a cluster."""
        file_path = node.file_path
        if not file_path:
            return None
        cluster_result = cluster_results[language]
        cluster_ids = cluster_result.get_clusters_for_file(file_path)
        for cid in cluster_ids:
            cluster_id = CodeBoardingClusterIds.qualify_local_id(
                CodeBoardingClusterIds.from_graph_id(cid), source_cluster_id_prefix
            )
            comp = cluster_to_component.get(cluster_id)
            if comp is not None:
                return comp
        return None

    def _assign_nodes_to_components(
        self,
        all_nodes: dict[tuple[str, str], Node],
        node_to_cluster: dict[tuple[str, str], str],
        cluster_to_component: dict[str, Component],
        cluster_results: dict[str, ClusterResult],
        fallback_component: Component,
        cfg_graphs: dict[str, CallGraph] | None = None,
        source_cluster_id_prefix: str = "",
    ) -> dict[str, list[Node]]:
        """Assign every node to a component via its cluster, file co-location, graph distance, or fallback."""
        component_nodes: dict[str, list[Node]] = defaultdict(list)
        unassigned: list[tuple[str, str]] = []

        for node_key, node in all_nodes.items():
            cid = node_to_cluster.get(node_key)
            if cid is not None and cid in cluster_to_component:
                component_nodes[cluster_to_component[cid].component_id].append(node)
            else:
                unassigned.append(node_key)

        if unassigned:
            logger.info(f"Assigning {len(unassigned)} orphan node(s)")

        assigned_by_file = 0
        assigned_by_graph = 0
        assigned_by_fallback = 0
        fallback_files: set[str] = set()

        # Pre-build undirected graphs once for all orphan lookups
        undirected_graphs = self._build_undirected_graphs(cluster_results, cfg_graphs) if unassigned else {}

        for language, qname in unassigned:
            node = all_nodes[(language, qname)]

            # 1. Try file co-location: if the node's file already belongs to a cluster/component
            comp = self._find_component_by_file(
                language,
                node,
                cluster_results,
                cluster_to_component,
                source_cluster_id_prefix,
            )
            if comp is not None:
                assigned_by_file += 1
                component_nodes[comp.component_id].append(node)
                continue

            # 2. Try graph distance: find the nearest cluster in the call graph
            nearest_cid = self._find_nearest_cluster(language, qname, cluster_results, undirected_graphs)
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

    def _log_node_coverage(self, analysis: AnalysisInsights, total_nodes: int) -> None:
        """Log the percentage of nodes assigned to components."""
        assigned_nodes = sum(len(fg.methods) for comp in analysis.components for fg in comp.file_methods)
        pct = (assigned_nodes / total_nodes * 100) if total_nodes else 0
        logger.info(f"Node coverage: {assigned_nodes}/{total_nodes} ({pct:.1f}%) nodes assigned to components")

    def populate_file_methods(
        self,
        analysis: AnalysisInsights,
        cluster_results: dict[str, ClusterResult],
        cfg_graphs: dict[str, CallGraph] | None = None,
        source_cluster_id_prefix: str = "",
    ) -> None:
        """Deterministically populate ``file_methods`` on every component.

        Node-centric approach guaranteeing 100% coverage:
        1. Build cluster_id -> component mapping from source_cluster_ids.
        2. Validate that all clusters are mapped (log error if not).
        3. For each node, assign via its cluster -> component mapping.
        4. Orphan nodes (not in any cluster) go to the nearest cluster's component
           or fall back to the first component.
        5. Build ``FileMethodGroup`` lists grouped by file path.

        Args:
            analysis: The analysis insights to populate.
            cluster_results: Language -> ClusterResult mapping.
            cfg_graphs: Optional scoped CallGraphs (e.g. subgraph from DetailsAgent).
                        When provided, only nodes from these graphs are considered,
                        preventing child components from exceeding parent scope.
        """
        # NOTE: These maps are intentionally rebuilt on each call — not cached — because
        # cluster_results differ per invocation (full graph in AbstractionAgent vs.
        # per-component subgraph in DetailsAgent, which runs in parallel).
        all_nodes = self._collect_all_cfg_nodes(cluster_results, cfg_graphs)
        cluster_to_component = self._build_cluster_to_component_map(analysis)
        node_to_cluster, all_cluster_ids = self._build_node_to_cluster_map(cluster_results, source_cluster_id_prefix)
        self._validate_cluster_coverage(cluster_to_component, all_cluster_ids)

        component_nodes = self._assign_nodes_to_components(
            all_nodes,
            node_to_cluster,
            cluster_to_component,
            cluster_results,
            analysis.components[0],
            cfg_graphs,
            source_cluster_id_prefix,
        )

        # One cache shared across the per-component method build and the files
        # index so each source file is read from disk once, not twice.
        source_cache: SourceCache = {}
        for comp in analysis.components:
            comp.file_methods = self._build_file_methods_from_nodes(
                component_nodes.get(comp.component_id, []), source_cache
            )

        analysis.files = build_files_index(analysis, self.repo_dir, source_cache)

        self._log_node_coverage(analysis, len(all_nodes))

    def build_static_relations(
        self,
        analysis: AnalysisInsights,
        cfg_graphs: dict[str, CallGraph] | None = None,
        source_cluster_id_prefix: str = "",
    ) -> None:
        """Build inter-component relations from CFG edges and merge with LLM relations.

        Static analysis supplies evidence for LLM-discovered architectural relations:
        - LLM + static match: keep LLM label and attach all matching edges.
        - LLM only with evidence/key_edges: keep as runtime or external communication.
        - Static only: keep out of user-facing relations unless the LLM selected the pair.

        If cfg_graphs is not provided, builds them from self.static_analysis.
        """
        if cfg_graphs is None:
            cfg_graphs = self.static_analysis.available_cfgs()
        node_to_component = build_node_to_component_map(analysis)
        static_relations = build_component_relations(node_to_component, cfg_graphs)
        analysis.components_relations = merge_relations(analysis.components_relations, static_relations, analysis)
        self._prefix_local_cluster_ids(analysis, source_cluster_id_prefix)

    def _prefix_local_cluster_ids(self, analysis: AnalysisInsights, prefix: str) -> None:
        """Prefix detail-subgraph cluster ids with their owning component scope."""
        for component in analysis.components:
            component.source_cluster_ids = CodeBoardingClusterIds.qualify_local_ids(
                component.source_cluster_ids, prefix
            )

    def build_scope_cfg_string(self, analysis: AnalysisInsights) -> str:
        """Render cross-component communication edges as a human-readable string for the LLM.

        For every CFG edge where src belongs to component A and dst belongs to
        component B (A != B), this produces a grouped summary like:

            ComponentA -> ComponentB (3 edges):
              src_pkg.MethodX -> dst_pkg.MethodY
              src_pkg.MethodZ -> dst_pkg.MethodW
        """
        node_to_component = build_node_to_component_map(analysis)
        id_to_name = {c.component_id: c.name for c in analysis.components}
        cfg_graphs = self.static_analysis.available_cfgs()
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
