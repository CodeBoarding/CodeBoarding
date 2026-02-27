import logging
import os
from pathlib import Path
import networkx as nx

from collections import defaultdict

from agents.agent_responses import Component, AnalysisInsights, MethodEntry, FileMethodGroup
from static_analyzer.analysis_result import StaticAnalysisResults
from static_analyzer.constants import Node, NodeType
from static_analyzer.graph import ClusterResult
from static_analyzer.cluster_helpers import get_all_cluster_ids

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
            cfg = self.static_analysis.get_cfg(lang)
            # Get cluster result for this language
            cluster_result = cluster_results.get(lang)
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
        logger.info("[ClusterMethodsMixin] Ensuring key_entities are unique across components")

        seen_entities: dict[str, Component] = {}

        for component in analysis.components:
            if component.name == "Unclassified":
                continue

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
                        logger.debug(
                            f"[ClusterMethodsMixin] Moved key_entity '{qname}' from {original_component.name} to {component.name}"
                        )
                    else:
                        # Keep in original component
                        entities_to_remove.append(key_entity)
                        logger.debug(
                            f"[ClusterMethodsMixin] Removed duplicate key_entity '{qname}' from {component.name} (kept in {original_component.name})"
                        )
                else:
                    seen_entities[qname] = component

            component.key_entities = [e for e in component.key_entities if e not in entities_to_remove]

    def _sanitize_component_cluster_ids(
        self,
        analysis: AnalysisInsights,
        valid_cluster_ids: set[int] | None = None,
        cluster_results: dict[str, ClusterResult] | None = None,
    ) -> None:
        """
        Sanitize cluster IDs in the analysis by removing invalid ones.
        Removes cluster IDs that don't exist in the static analysis.

        Args:
            analysis: The analysis to sanitize
            valid_cluster_ids: Optional set of valid IDs. If None, derives from cluster_results.
            cluster_results: dict mapping language -> ClusterResult. Required if valid_cluster_ids is None.
        """
        if valid_cluster_ids is None:
            if cluster_results is None:
                logger.error("Must provide either valid_cluster_ids or cluster_results")
                return
            valid_cluster_ids = get_all_cluster_ids(cluster_results)

        for component in analysis.components:
            if component.source_cluster_ids:
                original_ids = component.source_cluster_ids.copy()
                component.source_cluster_ids = [cid for cid in component.source_cluster_ids if cid in valid_cluster_ids]
                removed_ids = set(original_ids) - set(component.source_cluster_ids)
                if removed_ids:
                    logger.warning(
                        f"[ClusterMethodsMixin] Removed invalid cluster IDs {removed_ids} from component '{component.name}'"
                    )

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
            logger.warning(f"[ClusterMethodsMixin] Component {component.name} has no file_methods")
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
            logger.warning(
                f"[ClusterMethodsMixin] No CFG found for component {component.name} with {len(component_files)} files"
            )
            return "No relevant CFG clusters found for this component.", cluster_results

        return result, cluster_results

    def _collect_all_cfg_nodes(self, cluster_results: dict[str, ClusterResult]) -> dict[str, Node]:
        """Build a lookup of qualified_name -> Node for all languages present in cluster_results."""
        all_nodes: dict[str, Node] = {}
        for lang in cluster_results:
            cfg = self.static_analysis.get_cfg(lang)
            all_nodes.update(cfg.nodes)
        return all_nodes

    def _find_nearest_cluster(self, node_name: str, cluster_results: dict[str, ClusterResult]) -> int | None:
        """Find the cluster whose members are closest to *node_name* in the call graph.

        Uses undirected shortest-path distance so that both callers and callees
        are considered.  Returns the cluster_id of the nearest cluster, or None
        if the node is completely disconnected.
        """
        best_cluster: int | None = None
        best_dist = float("inf")

        for lang in cluster_results:
            cfg = self.static_analysis.get_cfg(lang)
            if node_name not in cfg.nodes:
                continue

            nx_graph = cfg.to_networkx().to_undirected()
            if node_name not in nx_graph:
                continue

            try:
                distances = nx.single_source_shortest_path_length(nx_graph, node_name)
            except nx.NetworkXError:
                continue

            cr = cluster_results[lang]
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
        allowed_types = NodeType.CALLABLE_TYPES | NodeType.CLASS_TYPES
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
                    node_type=node.type,
                )
            )

        groups: list[FileMethodGroup] = []
        for file_path in sorted(by_file):
            methods = sorted(by_file[file_path], key=lambda m: m.start_line)
            groups.append(FileMethodGroup(file_path=file_path, methods=methods))
        return groups

    def _build_cluster_to_component_map(self, analysis: AnalysisInsights) -> dict[int, Component]:
        """Build cluster_id → Component mapping from source_cluster_ids."""
        cluster_to_component: dict[int, Component] = {}
        for comp in analysis.components:
            for cid in comp.source_cluster_ids:
                cluster_to_component[cid] = comp
        return cluster_to_component

    def _build_node_to_cluster_map(self, cluster_results: dict[str, ClusterResult]) -> tuple[dict[str, int], set[int]]:
        """Build node_name → cluster_id mapping and collect all cluster IDs."""
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
                f"[ClusterMethodsMixin] {len(unmapped_cluster_ids)}/{len(all_cluster_ids)} clusters not mapped "
                f"via source_cluster_ids: {unmapped_cluster_ids}. This should never happen — all clusters must be "
                f"assigned to components by the LLM."
            )

    def _assign_nodes_to_components(
        self,
        all_nodes: dict[str, Node],
        node_to_cluster: dict[str, int],
        cluster_to_component: dict[int, Component],
        cluster_results: dict[str, ClusterResult],
        fallback_component: Component,
    ) -> dict[str, list[Node]]:
        """Assign every node to a component via its cluster, or by graph distance for orphans."""
        component_nodes: dict[str, list[Node]] = defaultdict(list)
        unassigned: list[str] = []

        for qname, node in all_nodes.items():
            cid = node_to_cluster.get(qname)
            if cid is not None and cid in cluster_to_component:
                component_nodes[cluster_to_component[cid].name].append(node)
            else:
                unassigned.append(qname)

        if unassigned:
            logger.info(f"[ClusterMethodsMixin] Assigning {len(unassigned)} orphan node(s) by graph distance")
        for qname in unassigned:
            nearest_cid = self._find_nearest_cluster(qname, cluster_results)
            if nearest_cid is not None and nearest_cid in cluster_to_component:
                comp = cluster_to_component[nearest_cid]
            else:
                comp = fallback_component
            component_nodes[comp.name].append(all_nodes[qname])

        return component_nodes

    def _log_node_coverage(self, analysis: AnalysisInsights, total_nodes: int) -> None:
        """Log the percentage of nodes assigned to components."""
        assigned_nodes = sum(len(fg.methods) for comp in analysis.components for fg in comp.file_methods)
        pct = (assigned_nodes / total_nodes * 100) if total_nodes else 0
        logger.info(
            f"[ClusterMethodsMixin] Node coverage: {assigned_nodes}/{total_nodes} ({pct:.1f}%) nodes assigned to components"
        )

    def populate_file_methods(self, analysis: AnalysisInsights, cluster_results: dict[str, ClusterResult]) -> None:
        """Deterministically populate ``file_methods`` on every component.

        Node-centric approach guaranteeing 100% coverage:
        1. Build cluster_id → component mapping from source_cluster_ids.
        2. Validate that all clusters are mapped (log error if not).
        3. For each node, assign via its cluster → component mapping.
        4. Orphan nodes (not in any cluster) go to the nearest cluster's component
           or fall back to the first component.
        5. Build ``FileMethodGroup`` lists grouped by file path.
        """
        all_nodes = self._collect_all_cfg_nodes(cluster_results)
        cluster_to_component = self._build_cluster_to_component_map(analysis)
        node_to_cluster, all_cluster_ids = self._build_node_to_cluster_map(cluster_results)
        self._validate_cluster_coverage(cluster_to_component, all_cluster_ids)

        component_nodes = self._assign_nodes_to_components(
            all_nodes, node_to_cluster, cluster_to_component, cluster_results, analysis.components[0]
        )

        for comp in analysis.components:
            comp.file_methods = self._build_file_methods_from_nodes(component_nodes.get(comp.name, []))

        self._log_node_coverage(analysis, len(all_nodes))
