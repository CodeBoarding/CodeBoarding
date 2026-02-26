import logging
import os
from pathlib import Path

from collections import defaultdict

from agents.agent_responses import Component, AnalysisInsights, MethodEntry, FileMethodGroup
from static_analyzer.analysis_result import StaticAnalysisResults
from static_analyzer.constants import Node
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
        1. If it's in the component's assigned_files -> keep it there (highest priority)
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

                    current_has_file = ref_file and any(
                        ref_file in assigned_file for assigned_file in component.assigned_files
                    )
                    original_has_file = ref_file and any(
                        ref_file in assigned_file for assigned_file in original_component.assigned_files
                    )

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
        Create a strict subgraph containing ONLY nodes from the component's assigned files.
        This ensures the analysis is strictly scoped to the component's boundaries.

        Args:
            component: Component with assigned_files to filter by

        Returns:
            Tuple of (formatted cluster string, cluster_results dict)
            where cluster_results maps language -> ClusterResult for the subgraph
        """
        if not component.assigned_files:
            logger.warning(f"[ClusterMethodsMixin] Component {component.name} has no assigned_files")
            return "No assigned files found for this component.", {}

        # Convert assigned files to absolute paths for comparison
        assigned_file_set = set()
        for f in component.assigned_files:
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
                f"[ClusterMethodsMixin] No CFG found for component {component.name} with {len(component.assigned_files)} assigned files"
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

    def _collect_clustered_node_names(self, cluster_results: dict[str, ClusterResult]) -> set[str]:
        """Return the set of all qualified names that belong to at least one cluster."""
        clustered: set[str] = set()
        for cr in cluster_results.values():
            for node_names in cr.clusters.values():
                clustered.update(node_names)
        return clustered

    def _find_nearest_cluster(self, node_name: str, cluster_results: dict[str, ClusterResult]) -> int | None:
        """Find the cluster whose members are closest to *node_name* in the call graph.

        Uses undirected shortest-path distance so that both callers and callees
        are considered.  Returns the cluster_id of the nearest cluster, or None
        if the node is completely disconnected.
        """
        import networkx as nx

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
        """Group a flat list of Nodes into FileMethodGroups sorted by file then line."""
        by_file: dict[str, list[MethodEntry]] = defaultdict(list)
        seen: set[str] = set()
        for node in nodes:
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

    def populate_file_methods(self, analysis: AnalysisInsights, cluster_results: dict[str, ClusterResult]) -> None:
        """Deterministically populate ``file_methods`` on every component.

        1. For each component, resolve all nodes from its ``source_cluster_ids``.
        2. Collect orphan nodes (present in the call-graph but not in any cluster).
        3. Assign each orphan to the component whose cluster is nearest by graph
           distance.
        4. Build ``FileMethodGroup`` lists grouped by file path.
        """
        all_nodes = self._collect_all_cfg_nodes(cluster_results)
        clustered_names = self._collect_clustered_node_names(cluster_results)

        # ---- Step 1: Resolve clustered nodes per component ----
        component_nodes: dict[str, list[Node]] = defaultdict(list)
        # Build cluster_id -> component mapping
        cluster_to_component: dict[int, Component] = {}
        for comp in analysis.components:
            for cid in comp.source_cluster_ids:
                cluster_to_component[cid] = comp
            # Gather nodes from this component's clusters
            for cid in comp.source_cluster_ids:
                for cr in cluster_results.values():
                    for qname in cr.get_nodes_for_cluster(cid):
                        node = all_nodes.get(qname)
                        if node:
                            component_nodes[comp.name].append(node)

        # ---- Step 2 & 3: Assign orphans to nearest cluster's component ----
        orphan_names = set(all_nodes.keys()) - clustered_names
        if orphan_names:
            logger.info(f"[ClusterMethodsMixin] Assigning {len(orphan_names)} orphan node(s) by graph distance")
        for qname in orphan_names:
            nearest_cid = self._find_nearest_cluster(qname, cluster_results)
            if nearest_cid is not None and nearest_cid in cluster_to_component:
                comp = cluster_to_component[nearest_cid]
                node = all_nodes[qname]
                component_nodes[comp.name].append(node)
                logger.debug(
                    f"[ClusterMethodsMixin] Orphan '{qname}' -> component '{comp.name}' (cluster {nearest_cid})"
                )
            else:
                logger.debug(f"[ClusterMethodsMixin] Orphan '{qname}' has no reachable cluster, skipping")

        # ---- Step 4: Build FileMethodGroup lists and derive assigned_files ----
        for comp in analysis.components:
            comp.file_methods = self._build_file_methods_from_nodes(component_nodes.get(comp.name, []))
            comp.assigned_files = [fg.file_path for fg in comp.file_methods]
