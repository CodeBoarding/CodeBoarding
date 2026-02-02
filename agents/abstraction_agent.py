import logging
import os.path
from pathlib import Path

from langchain_core.prompts import PromptTemplate

from agents.agent import LargeModelAgent
from agents.agent_responses import (
    AnalysisInsights,
    ClusterAnalysis,
    MetaAnalysisInsights,
)
from agents.prompts import (
    get_system_message,
    get_cluster_grouping_message,
    get_final_analysis_message,
)
from agents.cluster_methods_mixin import ClusterMethodsMixin
from agents.validation import (
    ValidationContext,
    validate_cluster_coverage,
    validate_component_relationships,
)
from monitoring import trace
from static_analyzer.analysis_result import StaticAnalysisResults
from static_analyzer.graph import ClusterResult
from static_analyzer.cluster_helpers import build_all_cluster_results, get_all_cluster_ids

logger = logging.getLogger(__name__)


class AbstractionAgent(ClusterMethodsMixin, LargeModelAgent):
    def __init__(
        self,
        repo_dir: Path,
        static_analysis: StaticAnalysisResults,
        project_name: str,
        meta_context: MetaAnalysisInsights,
    ):
        super().__init__(repo_dir, static_analysis, get_system_message())

        self.project_name = project_name
        self.meta_context = meta_context

        self.prompts = {
            "group_clusters": PromptTemplate(
                template=get_cluster_grouping_message(),
                input_variables=["project_name", "cfg_clusters", "meta_context", "project_type"],
            ),
            "final_analysis": PromptTemplate(
                template=get_final_analysis_message(),
                input_variables=["project_name", "cluster_analysis", "meta_context", "project_type"],
            ),
        }

    @trace
    def step_clusters_grouping(self, cluster_results: dict[str, ClusterResult]) -> ClusterAnalysis:
        logger.info(f"[AbstractionAgent] Grouping CFG clusters for: {self.project_name}")

        meta_context_str = self.meta_context.llm_str() if self.meta_context else "No project context available."
        project_type = self.meta_context.project_type if self.meta_context else "unknown"

        programming_langs = self.static_analysis.get_languages()

        # Build cluster string using the pre-computed cluster results
        cluster_str = self._build_cluster_string(programming_langs, cluster_results)

        prompt = self.prompts["group_clusters"].format(
            project_name=self.project_name,
            cfg_clusters=cluster_str,
            meta_context=meta_context_str,
            project_type=project_type,
        )

        cluster_analysis = self._validation_invoke(
            prompt,
            ClusterAnalysis,
            validators=[validate_cluster_coverage],
            context=ValidationContext(
                cluster_results=cluster_results,
                expected_cluster_ids=get_all_cluster_ids(cluster_results),
            ),
        )
        return cluster_analysis

    @trace
    def step_final_analysis(
        self, cluster_analysis: ClusterAnalysis, cluster_results: dict[str, ClusterResult]
    ) -> AnalysisInsights:
        logger.info(f"[AbstractionAgent] Generating final analysis for: {self.project_name}")

        meta_context_str = self.meta_context.llm_str() if self.meta_context else "No project context available."
        project_type = self.meta_context.project_type if self.meta_context else "unknown"

        cluster_str = cluster_analysis.llm_str() if cluster_analysis else "No cluster analysis available."

        prompt = self.prompts["final_analysis"].format(
            project_name=self.project_name,
            cluster_analysis=cluster_str,
            meta_context=meta_context_str,
            project_type=project_type,
        )

        # Build validation context with CFG graphs for edge checking
        context = ValidationContext(
            cluster_results=cluster_results,
            cfg_graphs={lang: self.static_analysis.get_cfg(lang) for lang in self.static_analysis.get_languages()},
        )

        return self._validation_invoke(
            prompt, AnalysisInsights, validators=[validate_component_relationships], context=context
        )

    def run(self):
        # Build full cluster results dict for all languages ONCE
        cluster_results = build_all_cluster_results(self.static_analysis)

        # Step 1: Group related clusters together into logical components
        cluster_analysis = self.step_clusters_grouping(cluster_results)

        # Step 2: Generate abstract components from grouped clusters
        analysis = self.step_final_analysis(cluster_analysis, cluster_results)
        # Step 3: Sanitize cluster IDs (remove invalid ones)
        self._sanitize_component_cluster_ids(analysis, cluster_results=cluster_results)
        # Step 4: Assign files to components (deterministic + LLM-based with validation)
        self.classify_files(analysis, cluster_results)
        # Step 5: Fix source code reference lines (resolves reference_file paths for key_entities)
        analysis = self.fix_source_code_reference_lines(analysis)
        # Step 6: Ensure unique key entities across components
        self._ensure_unique_key_entities(analysis)
        # Step 7: Ensure unique file assignments across components
        self._ensure_unique_file_assignments(analysis)

        return analysis, cluster_results
