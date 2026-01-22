import logging
import os
from pathlib import Path

from agents.agent_responses import Component, AnalysisInsights
from static_analyzer.analysis_result import StaticAnalysisResults
from static_analyzer.graph import ClusterResult

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
    """

    # These attributes must be provided by the class using this mixin
    repo_dir: Path
    static_analysis: StaticAnalysisResults

    def _get_cluster_result(self, lang: str) -> ClusterResult:
        """Get cached cluster result for a language."""
        cfg = self.static_analysis.get_cfg(lang)
        return cfg.cluster()

    def _get_files_for_clusters(self, cluster_ids: list[int]) -> set[str]:
        """
        Get all files that belong to the given cluster IDs.

        Args:
            cluster_ids: List of cluster IDs to get files for

        Returns:
            Set of file paths
        """
        files: set[str] = set()
        for lang in self.static_analysis.get_languages():
            cluster_result = self._get_cluster_result(lang)
            for cluster_id in cluster_ids:
                files.update(cluster_result.get_files_for_cluster(cluster_id))
        return files

    def _build_cluster_string(self, programming_langs: list[str], cluster_ids: set[int] | None = None) -> str:
        """
        Build a cluster string for LLM consumption.

        Args:
            programming_langs: List of languages to include
            cluster_ids: Optional set of cluster IDs to filter by

        Returns:
            Formatted cluster string with headers per language
        """
        cluster_lines = []

        for lang in programming_langs:
            cfg = self.static_analysis.get_cfg(lang)
            cluster_str = cfg.to_cluster_string(cluster_ids)

            if cluster_str.strip() and cluster_str not in ("empty", "none", "No clusters found."):
                header = "Component CFG" if cluster_ids else "Clusters"
                cluster_lines.append(f"\n## {lang.capitalize()} - {header}\n")
                cluster_lines.append(cluster_str)
                cluster_lines.append("\n")

        return "".join(cluster_lines)

    def _assign_files_to_component(self, component: Component) -> None:
        """
        Assign files to a component.
        1. Get all files from component's clusters (instant lookup)
        2. Add resolved key_entity files
        3. Convert to relative paths
        """
        assigned: set[str] = set()

        # Step 1: Files from clusters
        if component.source_cluster_ids:
            cluster_files = self._get_files_for_clusters(component.source_cluster_ids)
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

    def classify_files(self, analysis: AnalysisInsights) -> None:
        """
        Assign files to all components based on clusters and key_entities.
        Modifies the analysis object directly.
        """
        for comp in analysis.components:
            self._assign_files_to_component(comp)

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

    def _get_valid_cluster_ids(self) -> set[int]:
        """Get all valid cluster IDs from the static analysis across all languages."""
        valid_ids: set[int] = set()
        for lang in self.static_analysis.get_languages():
            cluster_result = self._get_cluster_result(lang)
            valid_ids.update(cluster_result.get_cluster_ids())
        return valid_ids

    def _validate_cluster_ids(self, analysis: AnalysisInsights, valid_cluster_ids: set[int] | None = None) -> None:
        """
        Validate and fix cluster IDs in the analysis.
        Removes invalid cluster IDs that don't exist in the static analysis.

        Args:
            analysis: The analysis to validate
            valid_cluster_ids: Optional set of valid IDs. If None, fetches from static analysis.
        """
        if valid_cluster_ids is None:
            valid_cluster_ids = self._get_valid_cluster_ids()

        for component in analysis.components:
            if component.source_cluster_ids:
                original_ids = component.source_cluster_ids.copy()
                component.source_cluster_ids = [cid for cid in component.source_cluster_ids if cid in valid_cluster_ids]
                removed_ids = set(original_ids) - set(component.source_cluster_ids)
                if removed_ids:
                    logger.warning(
                        f"[ClusterMethodsMixin] Removed invalid cluster IDs {removed_ids} from component '{component.name}'"
                    )

    def _validate_all_clusters_covered(self, analysis: AnalysisInsights) -> None:
        """
        Validate that all original cluster IDs from static analysis are covered in source_cluster_ids.

        This ensures no clusters from the static analysis are silently lost during the grouping process.
        Logs an error if any clusters are not referenced in any component.

        Args:
            analysis: The analysis to validate

        Raises:
            ValueError: If any clusters are not covered. The validator_agent will catch this
                        and communicate the issue back to the LLM for correction.
        """
        valid_cluster_ids = self._get_valid_cluster_ids()

        # Collect all cluster IDs referenced in the analysis
        covered_cluster_ids: set[int] = set()
        for component in analysis.components:
            if component.source_cluster_ids:
                covered_cluster_ids.update(component.source_cluster_ids)

        # Find uncovered clusters
        uncovered_ids = valid_cluster_ids - covered_cluster_ids

        if uncovered_ids:
            sorted_uncovered = sorted(uncovered_ids)
            logger.warning(
                f"[ClusterMethodsMixin] Uncovered cluster IDs: {sorted_uncovered}. "
                f"All {len(valid_cluster_ids)} clusters from static analysis must be covered."
            )
        else:
            logger.info(f"[ClusterMethodsMixin] All {len(valid_cluster_ids)} cluster IDs are covered in the analysis")
