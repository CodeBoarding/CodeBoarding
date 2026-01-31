import logging
import os
from pathlib import Path

from langchain_core.prompts import PromptTemplate

from agents.agent import LargeModelAgent
from agents.agent_responses import (
    AnalysisInsights,
    ClusterAnalysis,
    Component,
    MetaAnalysisInsights,
)
from agents.prompts import get_system_details_message, get_cfg_details_message, get_details_message
from agents.cluster_methods_mixin import ClusterMethodsMixin
from agents.validation import (
    ValidationContext,
    validate_cluster_coverage,
    validate_component_relationships,
    validate_key_entities,
)
from monitoring import trace
from static_analyzer.analysis_result import StaticAnalysisResults
from static_analyzer.cluster_helpers import get_all_cluster_ids

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
        }

    @trace
    def step_cluster_grouping(
        self, component: Component, subgraph_cluster_str: str, subgraph_cluster_results: dict
    ) -> ClusterAnalysis:
        """
        Group clusters within the component's subgraph into logical sub-components.

        Args:
            component: The component being analyzed
            subgraph_cluster_str: String representation of the component's CFG subgraph
            subgraph_cluster_results: Cluster results for the subgraph (from _create_strict_component_subgraph)

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

        # Build validation context using subgraph cluster results
        context = ValidationContext(
            cluster_results=subgraph_cluster_results,
            expected_cluster_ids=get_all_cluster_ids(subgraph_cluster_results),
        )

        cluster_analysis = self._validation_invoke(
            prompt, ClusterAnalysis, validators=[validate_cluster_coverage], context=context
        )
        return cluster_analysis

    @trace
    def step_final_analysis(
        self, component: Component, cluster_analysis: ClusterAnalysis, subgraph_cluster_results: dict
    ) -> AnalysisInsights:
        """
        Generate detailed final analysis from grouped clusters.

        Args:
            component: The component being analyzed
            cluster_analysis: The clustered structure from step_cluster_grouping
            subgraph_cluster_results: Cluster results for the subgraph (for validation)

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

        # Build validation context with subgraph CFG graphs for edge checking
        context = ValidationContext(
            cluster_results=subgraph_cluster_results,
            cfg_graphs={lang: self.static_analysis.get_cfg(lang) for lang in self.static_analysis.get_languages()},
        )

        return self._validation_invoke(
            prompt,
            AnalysisInsights,
            validators=[validate_component_relationships, validate_key_entities],
            context=context,
        )

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

        # Step 1: Create subgraph from component's assigned files using strict filtering
        subgraph_str, subgraph_cluster_results = self._create_strict_component_subgraph(component)

        # Step 2: Group clusters within the subgraph
        cluster_analysis = self.step_cluster_grouping(component, subgraph_str, subgraph_cluster_results)

        # Step 3: Generate detailed analysis from grouped clusters
        analysis = self.step_final_analysis(component, cluster_analysis, subgraph_cluster_results)

        # Step 4: Sanitize cluster IDs (remove invalid ones) - use subgraph's cluster results
        self._sanitize_component_cluster_ids(analysis, cluster_results=subgraph_cluster_results)

        # Step 5: Assign files to components (deterministic + LLM-based with validation)
        # Pass component's assigned files as scope to limit classification to this component
        self.classify_files(analysis, subgraph_cluster_results, scope_files=component.assigned_files)

        # Step 6: Fix source code reference lines (resolves reference_file paths)
        analysis = self.fix_source_code_reference_lines(analysis)

        # Step 7: Ensure unique key entities across components
        self._ensure_unique_key_entities(analysis)

        return analysis, subgraph_cluster_results
