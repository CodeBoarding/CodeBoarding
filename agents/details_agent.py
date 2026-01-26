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
from agents.validation import (
    ValidationContext,
    validate_cluster_coverage,
    validate_component_relationships,
)
from monitoring import trace
from static_analyzer.analysis_result import StaticAnalysisResults
from static_analyzer.cluster_helpers import get_all_cluster_ids
from static_analyzer.symbol_diff import SymbolDiff

logger = logging.getLogger(__name__)


# Prompt template for lightweight description update
DESCRIPTION_UPDATE_PROMPT = """You are updating the description of a software component based on implementation changes.

## Component Information
**Name:** {component_name}
**Current Description:** {current_description}

## Implementation Changes
The following functions/methods have had their implementations modified (but their signatures remain the same):
{changed_functions}

## Instructions
Based on the implementation changes described above, determine if the component description needs updating.

If the changes are minor bug fixes or performance improvements that don't change the component's purpose or behavior,
respond with the original description unchanged.

If the changes are significant enough to warrant a description update (e.g., new functionality within existing methods,
changed behavior, new integration points), provide an updated description that reflects these changes.

Respond with ONLY the updated description (or the original if no update needed). Do not include any other text."""


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
            prompt, AnalysisInsights, validators=[validate_component_relationships], context=context
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
        self.classify_files(analysis, subgraph_cluster_results)

        # Step 6: Fix source code reference lines (resolves reference_file paths)
        analysis = self.fix_source_code_reference_lines(analysis)

        # Step 7: Ensure unique key entities across components
        self._ensure_unique_key_entities(analysis)

        return analysis, subgraph_cluster_results

    @trace
    def update_description_only(
        self,
        component: Component,
        symbol_diffs: list[SymbolDiff],
    ) -> Component:
        """Update only the description of a component based on implementation changes.

        This is a lightweight operation for when only internal implementation changes
        are detected (no API changes). It avoids full re-analysis by just updating
        the description if needed.

        Args:
            component: The component to update
            symbol_diffs: List of SymbolDiff objects showing implementation changes

        Returns:
            Updated Component with potentially modified description
        """
        logger.info(f"[DetailsAgent] Lightweight description update for: {component.name}")

        # Collect all implementation-only changes
        changed_functions = []
        for diff in symbol_diffs:
            for symbol in diff.implementation_only:
                changed_functions.append(f"- {symbol.name} in {diff.file_path}")

        if not changed_functions:
            logger.info(f"[DetailsAgent] No implementation changes to describe for: {component.name}")
            return component

        # Format the changes for the prompt
        changes_str = "\n".join(changed_functions[:20])  # Limit to 20 for prompt size
        if len(changed_functions) > 20:
            changes_str += f"\n... and {len(changed_functions) - 20} more"

        prompt = DESCRIPTION_UPDATE_PROMPT.format(
            component_name=component.name,
            current_description=component.description,
            changed_functions=changes_str,
        )

        # Use simple invoke without structured output - just get text response
        try:
            updated_description = self._simple_invoke(prompt)
            updated_description = updated_description.strip()

            # Only update if the description actually changed
            if updated_description and updated_description != component.description:
                logger.info(f"[DetailsAgent] Description updated for: {component.name}")
                return Component(
                    name=component.name,
                    description=updated_description,
                    key_entities=component.key_entities,
                    assigned_files=component.assigned_files,
                    source_cluster_ids=component.source_cluster_ids,
                )
            else:
                logger.info(f"[DetailsAgent] Description unchanged for: {component.name}")
                return component
        except Exception as e:
            logger.warning(f"[DetailsAgent] Failed to update description for {component.name}: {e}")
            return component

    def _simple_invoke(self, prompt: str) -> str:
        """Simple LLM invoke without structured output parsing.

        This is used for simple text generation tasks like description updates.
        """
        from langchain_core.messages import HumanMessage

        response = self.llm.invoke([HumanMessage(content=prompt)])
        if hasattr(response, "content"):
            return str(response.content)
        return str(response)
