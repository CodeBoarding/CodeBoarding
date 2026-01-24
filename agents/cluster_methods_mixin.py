import logging
import os
from pathlib import Path

from agents.agent_responses import Component, AnalysisInsights
from static_analyzer.analysis_result import StaticAnalysisResults
from static_analyzer.graph import ClusterResult
from static_analyzer.cluster_helpers import get_files_for_cluster_ids, get_all_cluster_ids

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

    def _get_files_for_clusters(self, cluster_ids: list[int], cluster_results: dict[str, ClusterResult]) -> set[str]:
        """
        Get all files that belong to the given cluster IDs.

        Args:
            cluster_ids: List of cluster IDs to get files for
            cluster_results: dict mapping language -> ClusterResult

        Returns:
            Set of file paths
        """
        return get_files_for_cluster_ids(cluster_ids, cluster_results)

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

    def _assign_files_to_component(self, component: Component, cluster_results: dict[str, ClusterResult]) -> None:
        """
        Assign files to a component.
        1. Get all files from component's clusters (instant lookup)
        2. Add resolved key_entity files
        3. Convert to relative paths

        Args:
            component: Component to assign files to
            cluster_results: dict mapping language -> ClusterResult
        """
        assigned: set[str] = set()

        # Step 1: Files from clusters
        if component.source_cluster_ids:
            cluster_files = self._get_files_for_clusters(component.source_cluster_ids, cluster_results)
            assigned.update(cluster_files)

        # Step 2: Files from key_entities (already resolved by ReferenceResolverMixin)
        for entity in component.key_entities:
            if entity.reference_file:
                # Handle both absolute and relative paths
                if os.path.isabs(entity.reference_file):
                    assigned.add(entity.reference_file)
                else:
                    abs_path = os.path.join(self.repo_dir, entity.reference_file)
                    if os.path.exists(abs_path):
                        assigned.add(abs_path)
                    else:
                        assigned.add(entity.reference_file)

        # Convert to relative paths
        component.assigned_files = [os.path.relpath(f, self.repo_dir) if os.path.isabs(f) else f for f in assigned]

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
