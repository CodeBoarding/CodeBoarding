import logging
import os
from collections import defaultdict
from pathlib import Path

import networkx as nx

from agents.agent_responses import (
    AnalysisInsights,
    AnalysisStructure,
    ClusterAnalysis,
    ClustersComponent,
    Component,
    ComponentStructure,
    FileMethodGroup,
    MethodEntry,
    SourceCodeReference,
    assign_component_ids,
)
from static_analyzer.analysis_result import StaticAnalysisResults
from static_analyzer.cluster_helpers import (
    get_all_cluster_ids,
    get_files_for_cluster_ids,
)
from static_analyzer.cluster_relations import (
    build_component_relations,
    build_node_to_component_map,
    merge_relations,
)
from static_analyzer.constants import CALLABLE_TYPES, CLASS_TYPES, NodeType
from static_analyzer.graph import CallGraph, ClusterResult
from static_analyzer.node import Node

logger = logging.getLogger(__name__)


class ClusterMethodsMixin:
    """
    Mixin providing shared cluster-related functionality for agents.

    This mixin provides methods for:
    - Building cluster strings from CFG analysis (using CallGraph.cluster())
    - Assigning files to components based on clusters and key_entities
    - Ensuring unique key entities across components

    All clustering logic is delegated to CallGraph.cluster() which provides:
    - Deterministic cluster IDs (seed=42)
    - Cached results
    - File <-> cluster bidirectional mappings

    IMPORTANT: All methods are stateless with respect to ClusterResult.
    Cluster results must be passed explicitly as parameters.
    """

    # These attributes must be provided by the class using this mixin
    repo_dir: Path
    static_analysis: StaticAnalysisResults

    def _build_cluster_string(
        self,
        programming_langs: list[str],
        cluster_results: dict[str, ClusterResult],
        cluster_ids: set[int] | None = None,
    ) -> str:
        """
        Build a cluster string for LLM consumption using pre-computed cluster results.

        Args:
            programming_langs: List of languages to include
            cluster_results: Pre-computed cluster results mapping language -> ClusterResult
            cluster_ids: Optional set of cluster IDs to filter by

        Returns:
            Formatted cluster string with headers per language
        """
        cluster_lines = []

        for lang in programming_langs:
            cluster_result = cluster_results.get(lang)
            if cluster_result is None:
                continue

            cfg = self.static_analysis.get_cfg(lang)
            cluster_str = cfg.to_cluster_string(cluster_ids, cluster_result)

            if cluster_str.strip() and cluster_str not in ("empty", "none", "No clusters found."):
                header = "Component CFG" if cluster_ids else "Clusters"
                cluster_lines.append(f"\n## {lang.capitalize()} - {header}\n")
                cluster_lines.append(cluster_str)
                cluster_lines.append("\n")

        return "".join(cluster_lines)

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

                    component_files = [fg.file_path for fg in component.file_methods]
                    original_files = [fg.file_path for fg in original_component.file_methods]
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

    def _build_cluster_to_group_map(self, cluster_analysis: ClusterAnalysis) -> dict[int, ClustersComponent]:
        """Build cluster_id -> grouped cluster component mapping from ClusterAnalysis."""
        cluster_to_group: dict[int, ClustersComponent] = {}
        for group in cluster_analysis.cluster_components:
            for cluster_id in group.cluster_ids:
                cluster_to_group[cluster_id] = group
        return cluster_to_group

    def _build_cluster_adjacency_scores(
        self,
        cluster_results: dict[str, ClusterResult],
        cfg_graphs: dict[str, CallGraph] | None = None,
    ) -> dict[int, dict[int, int]]:
        """Build an undirected cluster-to-cluster edge strength map from CFG edges."""
        if cfg_graphs is None:
            cfg_graphs = {lang: self.static_analysis.get_cfg(lang) for lang in cluster_results}

        adjacency_scores: dict[int, dict[int, int]] = defaultdict(lambda: defaultdict(int))

        for lang, cfg in cfg_graphs.items():
            cluster_result = cluster_results.get(lang)
            if cluster_result is None:
                continue

            node_to_cluster: dict[str, int] = {}
            for cluster_id, members in cluster_result.clusters.items():
                for member in members:
                    node_to_cluster[member] = cluster_id

            for edge in cfg.edges:
                src_cluster = node_to_cluster.get(edge.get_source())
                dst_cluster = node_to_cluster.get(edge.get_destination())
                if src_cluster is None or dst_cluster is None or src_cluster == dst_cluster:
                    continue

                adjacency_scores[src_cluster][dst_cluster] += 1
                adjacency_scores[dst_cluster][src_cluster] += 1

        return {cluster_id: dict(scores) for cluster_id, scores in adjacency_scores.items()}

    def _select_best_component_for_cluster(
        self,
        cluster_id: int,
        cluster_analysis: ClusterAnalysis,
        adjacency_scores: dict[int, dict[int, int]],
    ) -> ClustersComponent | None:
        """Select the strongest existing group for a missing cluster, or None if ambiguous."""
        best_group: ClustersComponent | None = None
        best_score = 0
        tie = False

        for group in cluster_analysis.cluster_components:
            score = sum(adjacency_scores.get(cluster_id, {}).get(existing_id, 0) for existing_id in group.cluster_ids)
            if score > best_score:
                best_group = group
                best_score = score
                tie = False
            elif score > 0 and score == best_score:
                tie = True

        if best_score <= 0 or tie:
            return None

        return best_group

    def _auto_assign_missing_clusters(
        self,
        cluster_analysis: ClusterAnalysis,
        expected_cluster_ids: set[int],
        cluster_results: dict[str, ClusterResult],
        cfg_graphs: dict[str, CallGraph] | None = None,
    ) -> tuple[ClusterAnalysis, set[int]]:
        """Assign missing cluster IDs to the strongest connected existing group when unambiguous."""
        repaired = cluster_analysis.model_copy(deep=True)
        assigned_cluster_ids = set(self._build_cluster_to_group_map(repaired).keys())
        missing_cluster_ids = sorted(expected_cluster_ids - assigned_cluster_ids)
        if not missing_cluster_ids:
            return repaired, set()

        adjacency_scores = self._build_cluster_adjacency_scores(cluster_results, cfg_graphs)
        unresolved: set[int] = set()
        repaired_count = 0

        for cluster_id in missing_cluster_ids:
            best_group = self._select_best_component_for_cluster(cluster_id, repaired, adjacency_scores)
            if best_group is None:
                unresolved.add(cluster_id)
                continue

            best_group.cluster_ids.append(cluster_id)
            best_group.cluster_ids = sorted(set(best_group.cluster_ids))
            repaired_count += 1

        if repaired_count:
            logger.info(f"[Repair] Auto-assigned {repaired_count} missing cluster(s)")
        if unresolved:
            logger.info(f"[Repair] Could not auto-assign missing clusters: {sorted(unresolved)}")

        return repaired, unresolved

    def _resolve_cluster_ids_from_groups(self, analysis: AnalysisInsights, cluster_analysis: ClusterAnalysis) -> None:
        """Resolve source_cluster_ids deterministically from source_group_names via case-insensitive lookup."""
        group_name_to_ids: dict[str, list[int]] = {
            cc.name.lower(): cc.cluster_ids for cc in cluster_analysis.cluster_components
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
            component.source_cluster_ids = sorted(set(resolved_ids))

    def _create_strict_component_subgraph(self, component: Component) -> tuple[str, dict]:
        """
        Create a strict subgraph containing ONLY nodes from the component's file_methods.
        This ensures the analysis is strictly scoped to the component's boundaries.

        Args:
            component: Component with file_methods to filter by

        Returns:
            Tuple of (formatted cluster string, cluster_results dict)
            where cluster_results maps language -> ClusterResult for the subgraph
        """
        component_files = [fg.file_path for fg in component.file_methods]
        if not component_files:
            logger.warning(f"Component {component.name} has no file_methods")
            return "No assigned files found for this component.", {}

        # Convert files to absolute paths for comparison
        assigned_file_set = set()
        for f in component_files:
            abs_path = os.path.join(self.repo_dir, f) if not os.path.isabs(f) else f
            assigned_file_set.add(abs_path)

        result_parts = []
        cluster_results = {}

        for lang in self.static_analysis.get_languages():
            cfg = self.static_analysis.get_cfg(lang)

            # Use strict filtering logic
            sub_cfg = cfg.filter_by_files(assigned_file_set)

            if sub_cfg.nodes:
                # Calculate clusters for the subgraph
                sub_cluster_result = sub_cfg.cluster()
                cluster_results[lang] = sub_cluster_result

                cluster_str = sub_cfg.to_cluster_string(cluster_result=sub_cluster_result)
                if cluster_str.strip() and cluster_str not in ("empty", "none", "No clusters found."):
                    result_parts.append(f"\n## {lang.capitalize()} - Component CFG\n")
                    result_parts.append(cluster_str)
                    result_parts.append("\n")

        result = "".join(result_parts)

        if not result.strip():
            logger.warning(f"No CFG found for component {component.name} with {len(component_files)} files")
            return "No relevant CFG clusters found for this component.", cluster_results

        return result, cluster_results

    def _build_analysis_from_structure(self, structure: AnalysisStructure) -> AnalysisInsights:
        """Convert structure-only LLM output into AnalysisInsights for deterministic enrichment."""
        components = [
            Component(
                name=component.name,
                description=component.description,
                key_entities=[],
                source_group_names=list(component.source_group_names),
            )
            for component in structure.components
        ]
        return AnalysisInsights(
            description=structure.description,
            components=components,
            components_relations=[],
        )

    def _collect_component_candidate_nodes(
        self,
        component: Component,
        cluster_results: dict[str, ClusterResult],
    ) -> list[Node]:
        """Collect candidate callable/class nodes for deterministic key-entity population."""
        all_nodes = self._collect_all_cfg_nodes(cluster_results)
        candidate_names: set[str] = set()

        for cluster_result in cluster_results.values():
            for cluster_id in component.source_cluster_ids:
                candidate_names.update(cluster_result.clusters.get(cluster_id, set()))

        for file_group in component.file_methods:
            for method in file_group.methods:
                candidate_names.add(method.qualified_name)

        candidates: list[Node] = []
        for qualified_name in sorted(candidate_names):
            node = all_nodes.get(qualified_name)
            if node is None or node.type not in CALLABLE_TYPES | CLASS_TYPES:
                continue
            candidates.append(node)

        return candidates

    def _rank_component_entities(
        self,
        nodes: list[Node],
        cfg_graphs: dict[str, CallGraph] | None = None,
    ) -> list[Node]:
        """Rank candidate nodes by architectural importance for key-entity selection."""
        if cfg_graphs is None:
            cfg_graphs = {lang: self.static_analysis.get_cfg(lang) for lang in self.static_analysis.get_languages()}

        degree_by_name: dict[str, int] = defaultdict(int)
        for cfg in cfg_graphs.values():
            for edge in cfg.edges:
                degree_by_name[edge.get_source()] += 1
                degree_by_name[edge.get_destination()] += 1

        def sort_key(node: Node) -> tuple[int, int, str]:
            is_class = 1 if node.type in CLASS_TYPES else 0
            return (-is_class, -degree_by_name.get(node.fully_qualified_name, 0), node.fully_qualified_name)

        return sorted(nodes, key=sort_key)

    def _build_source_code_references(self, nodes: list[Node], limit: int = 5) -> list[SourceCodeReference]:
        """Convert ranked nodes into SourceCodeReference objects."""
        references: list[SourceCodeReference] = []
        for node in nodes[:limit]:
            file_path = (
                os.path.relpath(node.file_path, self.repo_dir) if os.path.isabs(node.file_path) else node.file_path
            )
            references.append(
                SourceCodeReference(
                    qualified_name=node.fully_qualified_name,
                    reference_file=file_path,
                    reference_start_line=node.line_start,
                    reference_end_line=node.line_end,
                )
            )
        return references

    def _populate_key_entities(
        self,
        analysis: AnalysisInsights,
        cluster_results: dict[str, ClusterResult],
        fill_missing_only: bool = True,
    ) -> None:
        """Populate key_entities deterministically from static analysis."""
        cfg_graphs = {lang: self.static_analysis.get_cfg(lang) for lang in cluster_results}

        for component in analysis.components:
            if fill_missing_only and component.key_entities:
                continue

            candidates = self._collect_component_candidate_nodes(component, cluster_results)
            ranked_candidates = self._rank_component_entities(candidates, cfg_graphs)
            component.key_entities = self._build_source_code_references(ranked_candidates, limit=5)

    def _materialize_analysis(
        self,
        structure: AnalysisStructure,
        cluster_analysis: ClusterAnalysis,
        cluster_results: dict[str, ClusterResult],
        parent_id: str = "",
    ) -> AnalysisInsights:
        """Materialize structure-only LLM output into final analysis with deterministic enrichment."""
        analysis = self._build_analysis_from_structure(structure)
        assign_component_ids(analysis, parent_id=parent_id)
        self._resolve_cluster_ids_from_groups(analysis, cluster_analysis)
        self.populate_file_methods(analysis, cluster_results)
        self._populate_key_entities(analysis, cluster_results)
        self.build_static_relations(analysis)
        analysis = getattr(self, "fix_source_code_reference_lines")(analysis)
        self._ensure_unique_key_entities(analysis)
        return analysis

    def _collect_all_cfg_nodes(self, cluster_results: dict[str, ClusterResult]) -> dict[str, Node]:
        """Build a lookup of qualified_name -> Node for all languages present in cluster_results.

        NOTE: Caching belongs here (not in callers) since cfg.nodes for a given
        language is immutable within a run.  Currently cheap enough that the
        dict merge dominates, but a per-language cache could be added if profiling
        shows this is a bottleneck.
        """
        all_nodes: dict[str, Node] = {}
        for lang in cluster_results:
            cfg = self.static_analysis.get_cfg(lang)
            all_nodes.update(cfg.nodes)
        return all_nodes

    def _build_undirected_graphs(self, cluster_results: dict[str, ClusterResult]) -> dict[str, nx.Graph]:
        """Pre-build undirected networkx graphs for each language in cluster_results.

        Meant to be called once before iterating over orphan nodes, so that
        ``_find_nearest_cluster`` doesn't rebuild the graph on every call.
        """
        graphs: dict[str, nx.Graph] = {}
        for lang in cluster_results:
            cfg = self.static_analysis.get_cfg(lang)
            graphs[lang] = cfg.to_networkx().to_undirected()
        return graphs

    def _find_nearest_cluster(
        self,
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

    def _build_file_methods_from_nodes(self, nodes: list[Node]) -> list[FileMethodGroup]:
        """Group a flat list of Nodes into FileMethodGroups sorted by file then line.

        Only includes methods, functions, and classes/interfaces — variables,
        constants, properties, and fields are excluded.
        """
        allowed_types = CALLABLE_TYPES | CLASS_TYPES
        by_file: dict[str, list[MethodEntry]] = defaultdict(list)
        seen: set[str] = set()
        for node in nodes:
            if node.type not in allowed_types:
                continue
            if node.fully_qualified_name in seen:
                continue
            seen.add(node.fully_qualified_name)
            rel_path = (
                os.path.relpath(node.file_path, self.repo_dir) if os.path.isabs(node.file_path) else node.file_path
            )
            by_file[rel_path].append(
                MethodEntry(
                    qualified_name=node.fully_qualified_name,
                    start_line=node.line_start,
                    end_line=node.line_end,
                    node_type=node.type.name,
                )
            )

        groups: list[FileMethodGroup] = []
        for file_path in sorted(by_file):
            methods = sorted(by_file[file_path], key=lambda m: m.start_line)
            groups.append(FileMethodGroup(file_path=file_path, methods=methods))
        return groups

    def _build_cluster_to_component_map(self, analysis: AnalysisInsights) -> dict[int, Component]:
        """Build cluster_id -> Component mapping from source_cluster_ids."""
        cluster_to_component: dict[int, Component] = {}
        for comp in analysis.components:
            for cid in comp.source_cluster_ids:
                cluster_to_component[cid] = comp
        return cluster_to_component

    def _build_node_to_cluster_map(self, cluster_results: dict[str, ClusterResult]) -> tuple[dict[str, int], set[int]]:
        """Build node_name (qualified name) -> cluster_id mapping and collect all cluster IDs."""
        all_cluster_ids: set[int] = set()
        node_to_cluster: dict[str, int] = {}
        for cr in cluster_results.values():
            for cid, members in cr.clusters.items():
                all_cluster_ids.add(cid)
                for name in members:
                    node_to_cluster[name] = cid
        return node_to_cluster, all_cluster_ids

    def _validate_cluster_coverage(self, cluster_to_component: dict[int, Component], all_cluster_ids: set[int]) -> None:
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
        node: Node,
        cluster_results: dict[str, ClusterResult],
        cluster_to_component: dict[int, Component],
    ) -> Component | None:
        """Try to assign a node to a component based on its file already belonging to a cluster."""
        file_path = node.file_path
        if not file_path:
            return None
        for cr in cluster_results.values():
            cluster_ids = cr.get_clusters_for_file(file_path)
            for cid in cluster_ids:
                comp = cluster_to_component.get(cid)
                if comp is not None:
                    return comp
        return None

    def _assign_nodes_to_components(
        self,
        all_nodes: dict[str, Node],
        node_to_cluster: dict[str, int],
        cluster_to_component: dict[int, Component],
        cluster_results: dict[str, ClusterResult],
        fallback_component: Component,
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
        undirected_graphs = self._build_undirected_graphs(cluster_results) if unassigned else {}

        for qname in unassigned:
            node = all_nodes[qname]

            # 1. Try file co-location: if the node's file already belongs to a cluster/component
            comp = self._find_component_by_file(node, cluster_results, cluster_to_component)
            if comp is not None:
                assigned_by_file += 1
                component_nodes[comp.component_id].append(node)
                continue

            # 2. Try graph distance: find the nearest cluster in the call graph
            nearest_cid = self._find_nearest_cluster(qname, cluster_results, undirected_graphs)
            if nearest_cid is not None and nearest_cid in cluster_to_component:
                comp = cluster_to_component[nearest_cid]
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
            logger.warning(
                f"{assigned_by_fallback} node(s) fell back to '{fallback_component.name}' "
                f"— files: {sorted(fallback_files)}"
            )

        return component_nodes

    def _log_node_coverage(self, analysis: AnalysisInsights, total_nodes: int) -> None:
        """Log the percentage of nodes assigned to components."""
        assigned_nodes = sum(len(fg.methods) for comp in analysis.components for fg in comp.file_methods)
        pct = (assigned_nodes / total_nodes * 100) if total_nodes else 0
        logger.info(f"Node coverage: {assigned_nodes}/{total_nodes} ({pct:.1f}%) nodes assigned to components")

    def populate_file_methods(self, analysis: AnalysisInsights, cluster_results: dict[str, ClusterResult]) -> None:
        """Deterministically populate ``file_methods`` on every component.

        Node-centric approach guaranteeing 100% coverage:
        1. Build cluster_id -> component mapping from source_cluster_ids.
        2. Validate that all clusters are mapped (log error if not).
        3. For each node, assign via its cluster -> component mapping.
        4. Orphan nodes (not in any cluster) go to the nearest cluster's component
           or fall back to the first component.
        5. Build ``FileMethodGroup`` lists grouped by file path.
        """
        if not analysis.components:
            logger.info("Skipping file_methods population because analysis has no components")
            return

        # NOTE: These maps are intentionally rebuilt on each call — not cached — because
        # cluster_results differ per invocation (full graph in AbstractionAgent vs.
        # per-component subgraph in DetailsAgent, which runs in parallel).
        all_nodes = self._collect_all_cfg_nodes(cluster_results)
        cluster_to_component = self._build_cluster_to_component_map(analysis)
        node_to_cluster, all_cluster_ids = self._build_node_to_cluster_map(cluster_results)
        self._validate_cluster_coverage(cluster_to_component, all_cluster_ids)

        component_nodes = self._assign_nodes_to_components(
            all_nodes, node_to_cluster, cluster_to_component, cluster_results, analysis.components[0]
        )

        for comp in analysis.components:
            comp.file_methods = self._build_file_methods_from_nodes(component_nodes.get(comp.component_id, []))

        self._log_node_coverage(analysis, len(all_nodes))

    def build_static_relations(
        self,
        analysis: AnalysisInsights,
        cfg_graphs: dict[str, CallGraph] | None = None,
    ) -> None:
        """Build inter-component relations from CFG edges and merge with LLM relations.

        Replaces LLM-only relations with statically-backed ones:
        - LLM + static match: keep LLM label, attach edge_count
        - LLM only (no static backing): drop
        - Static only: add with auto-label "calls"

        If cfg_graphs is not provided, builds them from self.static_analysis.
        """
        if cfg_graphs is None:
            cfg_graphs = {lang: self.static_analysis.get_cfg(lang) for lang in self.static_analysis.get_languages()}
        node_to_component = build_node_to_component_map(analysis)
        static_relations = build_component_relations(node_to_component, cfg_graphs)
        analysis.components_relations = merge_relations(analysis.components_relations, static_relations, analysis)
