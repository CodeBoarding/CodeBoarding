import logging
import os
from pathlib import Path

from langchain_core.prompts import PromptTemplate

from agents.agent import LargeModelAgent
from agents.agent_responses import (
    AnalysisInsights,
    ClusterAnalysis,
    ValidationInsights,
    Component,
    MetaAnalysisInsights,
)
from agents.prompts import (
    get_system_details_message,
    get_cfg_details_message,
    get_details_message,
    get_feedback_message,
)
from agents.cluster_methods_mixin import ClusterMethodsMixin
from monitoring import trace
from static_analyzer.analysis_result import StaticAnalysisResults

logger = logging.getLogger(__name__)


class DetailsAgent(ClusterMethodsMixin, LargeModelAgent):
    def __init__(
        self,
        repo_dir: Path,
        static_analysis: StaticAnalysisResults,
        project_name: str,
        meta_context: MetaAnalysisInsights,
    ):
        super().__init__(repo_dir, static_analysis, get_system_details_message())
        self.project_name = project_name
        self.meta_context = meta_context

        self.prompts = {
            "group_clusters": PromptTemplate(
                template=get_cfg_details_message(),
                input_variables=["project_name", "cfg_str", "component", "meta_context", "project_type"],
            ),
            "final_analysis": PromptTemplate(
                template=get_details_message(),
                input_variables=["insight_so_far", "component", "meta_context", "project_type"],
            ),
            "feedback": PromptTemplate(template=get_feedback_message(), input_variables=["analysis", "feedback"]),
        }

    def _create_subgraph_from_component(self, component: Component) -> tuple[str, dict]:
        """
        Create a subgraph containing only nodes from the component's assigned files.

        Args:
            component: Component with assigned_files to filter by

        Returns:
            Tuple of (formatted cluster string, cluster_results dict)
            where cluster_results maps language -> ClusterResult for the subgraph
        """
        if not component.assigned_files:
            logger.warning(f"[DetailsAgent] Component {component.name} has no assigned_files")
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
            cluster_result = cfg.cluster()

            # Find all cluster IDs that contain files from assigned_files
            relevant_cluster_ids = set()
            for cluster_id in cluster_result.get_cluster_ids():
                cluster_files = cluster_result.get_files_for_cluster(cluster_id)
                # If any file in this cluster is in assigned_files, include the cluster
                if cluster_files & assigned_file_set:
                    relevant_cluster_ids.add(cluster_id)

            if not relevant_cluster_ids:
                continue

            # Create subgraph with only relevant clusters
            sub_cfg = cfg.subgraph(relevant_cluster_ids)

            if sub_cfg.nodes:
                # Get the cluster result for the subgraph
                sub_cluster_result = sub_cfg.cluster()
                cluster_results[lang] = sub_cluster_result

                cluster_str = sub_cfg.to_cluster_string()
                if cluster_str.strip() and cluster_str not in ("empty", "none", "No clusters found."):
                    result_parts.append(f"\n## {lang.capitalize()} - Component CFG\n")
                    result_parts.append(cluster_str)
                    result_parts.append("\n")

        result = "".join(result_parts)

        if not result.strip():
            logger.warning(
                f"[DetailsAgent] No CFG found for component {component.name} with {len(component.assigned_files)} assigned files"
            )
            return "No relevant CFG clusters found for this component.", cluster_results

        return result, cluster_results

    @trace
    def step_cluster_grouping(self, component: Component, subgraph_cluster_str: str) -> ClusterAnalysis:
        """
        Group clusters within the component's subgraph into logical sub-components.

        Args:
            component: The component being analyzed
            subgraph_cluster_str: String representation of the component's CFG subgraph

        Returns:
            ClusterAnalysis with grouped clusters for this component
        """
        logger.info(f"[DetailsAgent] Grouping clusters for component: {component.name}")
        meta_context_str = self.meta_context.llm_str() if self.meta_context else "No project context available."
        project_type = self.meta_context.project_type if self.meta_context else "unknown"

        prompt = self.prompts["group_clusters"].format(
            project_name=self.project_name,
            cfg_str=subgraph_cluster_str,
            component=component.llm_str(),
            meta_context=meta_context_str,
            project_type=project_type,
        )
        cluster_analysis = self._parse_invoke(prompt, ClusterAnalysis)
        return cluster_analysis

    @trace
    def step_final_analysis(self, component: Component, cluster_analysis: ClusterAnalysis) -> AnalysisInsights:
        """
        Generate detailed final analysis from grouped clusters.

        Args:
            component: The component being analyzed
            cluster_analysis: The clustered structure from step_cluster_grouping

        Returns:
            AnalysisInsights with detailed component information
        """
        logger.info(f"[DetailsAgent] Generating final detailed analysis for: {component.name}")
        meta_context_str = self.meta_context.llm_str() if self.meta_context else "No project context available."
        project_type = self.meta_context.project_type if self.meta_context else "unknown"

        cluster_str = cluster_analysis.llm_str() if cluster_analysis else "No cluster analysis available."

        prompt = self.prompts["final_analysis"].format(
            insight_so_far=cluster_str,
            component=component.llm_str(),
            meta_context=meta_context_str,
            project_type=project_type,
        )
        return self._parse_invoke(prompt, AnalysisInsights)

    @trace
    def apply_feedback(self, analysis: AnalysisInsights, feedback: ValidationInsights) -> AnalysisInsights:
        logger.info(f"[DetailsAgent] Applying feedback to analysis for project: {self.project_name}")
        prompt = self.prompts["feedback"].format(analysis=analysis.llm_str(), feedback=feedback.llm_str())
        analysis = self._parse_invoke(prompt, AnalysisInsights)
        return self.fix_source_code_reference_lines(analysis)

    def run(self, component: Component):
        """
        Analyze a component in detail by creating a subgraph and analyzing its structure.

        This follows the same pattern as AbstractionAgent but operates on a component-level
        subgraph instead of the full codebase.

        Args:
            component: Component to analyze in detail

        Returns:
            Tuple of (AnalysisInsights, cluster_results dict) with detailed component information
        """
        logger.info(f"[DetailsAgent] Processing component: {component.name}")

        # Step 1: Create subgraph from component's assigned files
        subgraph_str, subgraph_cluster_results = self._create_subgraph_from_component(component)

        # Step 2: Group clusters within the subgraph
        cluster_analysis = self.step_cluster_grouping(component, subgraph_str)

        # Step 3: Generate detailed analysis from grouped clusters
        analysis = self.step_final_analysis(component, cluster_analysis)

        # Step 4: Sanitize cluster IDs (remove invalid ones) - use subgraph's cluster results
        self._sanitize_component_cluster_ids(analysis, cluster_results=subgraph_cluster_results)

        # Step 5: Assign files to components (deterministic + LLM-based) - use subgraph's cluster results
        self.classify_files(analysis, subgraph_cluster_results)

        # Step 6: Fix source code reference lines (resolves reference_file paths)
        analysis = self.fix_source_code_reference_lines(analysis)

        # Step 7: Ensure unique key entities across components
        self._ensure_unique_key_entities(analysis)

        return analysis, subgraph_cluster_results
