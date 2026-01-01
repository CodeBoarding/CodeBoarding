import logging
import os
from collections import defaultdict
from pathlib import Path

from agents.agent_responses import Component, AnalysisInsights
from static_analyzer.analysis_result import StaticAnalysisResults

logger = logging.getLogger(__name__)


class ClusterMethodsMixin:
    """
    Mixin providing shared cluster-related functionality for agents.

    This mixin provides methods for:
    - Building cluster strings from CFG analysis
    - Extracting specific clusters from cluster strings
    - Mapping files to clusters
    - Matching files to components based on clusters
    - Ensuring unique key entities across components
    """

    # These attributes must be provided by the class using this mixin
    repo_dir: Path
    static_analysis: StaticAnalysisResults

    def _build_cluster_string(self, programming_langs: list[str]) -> str:
        """
        Build a cluster string that explicitly shows cluster IDs and their nodes.
        This makes it easy for the LLM to reference clusters.
        """
        cluster_lines = []

        for lang in programming_langs:
            cfg = self.static_analysis.get_cfg(lang)
            cluster_str = cfg.to_cluster_string()

            # The cluster string already has format: "Cluster 1 (5 nodes): [...]"
            # This is perfect - LLM can see cluster IDs explicitly
            cluster_lines.append(f"\n## {lang.capitalize()} Clusters\n")
            cluster_lines.append(cluster_str)

        return "".join(cluster_lines)

    def _extract_clusters_from_string(self, cluster_str: str, cluster_ids: set[int]) -> str:
        """
        Parse cluster string and extract only specified cluster IDs.
        This is deterministic - no LLM call needed!
        """
        lines = cluster_str.split("\n")
        result_lines = []
        include_current = False

        for line in lines:
            # Check if this is a cluster header: "Cluster N (...)"
            if line.startswith("Cluster "):
                parts = line.split()
                if len(parts) >= 2:
                    try:
                        cluster_num = int(parts[1])
                        include_current = cluster_num in cluster_ids
                    except ValueError:
                        include_current = False

            if include_current:
                result_lines.append(line)

        return "\n".join(result_lines)

    def _build_file_cluster_mapping(self) -> dict[str, set[int]]:
        """
        Build mapping of file_path -> set of cluster_ids that have nodes in that file.
        This is purely deterministic - no LLM needed.
        """
        file_to_clusters: dict[str, set[int]] = defaultdict(set)

        for lang in self.static_analysis.get_languages():
            cfg = self.static_analysis.get_cfg(lang)
            nx_graph = cfg.to_networkx()

            if nx_graph.number_of_nodes() == 0:
                continue

            # Get cluster mapping
            communities, _ = cfg._adaptive_clustering(
                nx_graph,
                target_clusters=20,  # Use same default as to_cluster_string()
                min_cluster_size=2,
            )

            for cluster_id, nodes in enumerate(communities, start=1):  # Start from 1 to match display
                if len(nodes) < 2:  # Skip singletons
                    continue
                for node_name in nodes:
                    # Get file path for this node
                    if node_name in nx_graph.nodes:
                        node_data = nx_graph.nodes[node_name]
                        file_path = node_data.get("file_path")
                        if file_path:
                            file_to_clusters[file_path].add(cluster_id)

        return file_to_clusters

    def _match_file_to_components(
        self, file_path: str, components: list[Component], file_to_clusters: dict[str, set[int]]
    ) -> list[Component]:
        """
        Match a file to ALL components it belongs to deterministically.

        A file can belong to multiple components since:
        - Shared utilities may be used by multiple components
        - Files can contain code referenced by different components

        Matching logic:
        1. If file contains a key_entity from component -> match
        2. If file's clusters overlap with component.source_cluster_ids -> match
        3. Otherwise -> no match (goes to Unclassified)

        Returns: List of all matching components (can be empty)
        """
        file_clusters = file_to_clusters.get(file_path, set())
        matched_components = []

        for component in components:
            if component.name == "Unclassified":
                continue

            # Check 1: Does file contain any key entities?
            for key_entity in component.key_entities:
                if key_entity.reference_file:
                    # Normalize paths for comparison
                    ref_file_norm = os.path.normpath(key_entity.reference_file)
                    file_path_norm = os.path.normpath(file_path)
                    if file_path_norm.endswith(ref_file_norm) or ref_file_norm in file_path_norm:
                        matched_components.append(component)
                        break  # Don't need to check other key_entities for this component

            # Check 2: Do file's clusters overlap with component's clusters?
            # Only check if not already matched via key_entities
            if component not in matched_components and file_clusters and component.source_cluster_ids:
                if any(cluster_id in component.source_cluster_ids for cluster_id in file_clusters):
                    matched_components.append(component)

        return matched_components

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
        logger.info(f"[ClusterMethodsMixin] Ensuring key_entities are unique across components")

        # Track which qualified_names we've seen and where
        seen_entities: dict[str, Component] = {}

        for component in analysis.components:
            if component.name == "Unclassified":
                continue

            entities_to_remove = []

            for key_entity in component.key_entities:
                qname = key_entity.qualified_name

                if qname in seen_entities:
                    # Already assigned to another component
                    original_component = seen_entities[qname]

                    # Decide which component should keep this entity
                    # Priority: Keep it in the component where the file is assigned
                    ref_file = key_entity.reference_file

                    current_has_file = ref_file and any(
                        ref_file in assigned_file for assigned_file in component.assigned_files
                    )
                    original_has_file = ref_file and any(
                        ref_file in assigned_file for assigned_file in original_component.assigned_files
                    )

                    if current_has_file and not original_has_file:
                        # Move to current component (remove from original)
                        original_component.key_entities = [
                            e for e in original_component.key_entities if e.qualified_name != qname
                        ]
                        seen_entities[qname] = component
                        logger.debug(
                            f"[ClusterMethodsMixin] Moved key_entity '{qname}' from {original_component.name} to {component.name}"
                        )
                    else:
                        # Keep in original component (remove from current)
                        entities_to_remove.append(key_entity)
                        logger.debug(
                            f"[ClusterMethodsMixin] Removed duplicate key_entity '{qname}' from {component.name} (kept in {original_component.name})"
                        )
                else:
                    # First time seeing this entity
                    seen_entities[qname] = component

            # Remove duplicates from current component
            component.key_entities = [e for e in component.key_entities if e not in entities_to_remove]
