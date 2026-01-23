import logging
import os.path
from pathlib import Path

from langchain_core.prompts import PromptTemplate

from agents.agent import LargeModelAgent
from agents.agent_responses import (
    AnalysisInsights,
    ClusterAnalysis,
    ValidationInsights,
    MetaAnalysisInsights,
)
from agents.prompts import (
    get_system_message,
    get_cluster_grouping_message,
    get_final_analysis_message,
    get_feedback_message,
)
from agents.cluster_methods_mixin import ClusterMethodsMixin
from monitoring import trace
from static_analyzer.analysis_result import StaticAnalysisResults
from static_analyzer.cluster_helpers import build_all_cluster_results

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
            "feedback": PromptTemplate(template=get_feedback_message(), input_variables=["analysis", "feedback"]),
        }

    @trace
    def step_clusters_grouping(self) -> ClusterAnalysis:
        logger.info(f"[AbstractionAgent] Grouping CFG clusters for: {self.project_name}")

        meta_context_str = self.meta_context.llm_str() if self.meta_context else "No project context available."
        project_type = self.meta_context.project_type if self.meta_context else "unknown"

        programming_langs = self.static_analysis.get_languages()

        # Build cluster string that explicitly shows cluster IDs
        cluster_str = self._build_cluster_string(programming_langs)

        prompt = self.prompts["group_clusters"].format(
            project_name=self.project_name,
            cfg_clusters=cluster_str,
            meta_context=meta_context_str,
            project_type=project_type,
        )

        cluster_analysis = self._parse_invoke(prompt, ClusterAnalysis)
        return cluster_analysis

    @trace
    def step_final_analysis(self, cluster_analysis: ClusterAnalysis) -> AnalysisInsights:
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

        return self._parse_invoke(prompt, AnalysisInsights)

    @trace
    def apply_feedback(self, analysis: AnalysisInsights, feedback: ValidationInsights) -> AnalysisInsights:
        logger.info(f"[AbstractionAgent] Applying feedback to analysis for project: {self.project_name}")
        prompt = self.prompts["feedback"].format(analysis=analysis.llm_str(), feedback=feedback.llm_str())
        analysis = self._parse_invoke(prompt, AnalysisInsights)
        return self.fix_source_code_reference_lines(analysis)

    def run(self):
        # Build full cluster results dict for all languages
        cluster_results = build_all_cluster_results(self.static_analysis)

        # Step 1: Group related clusters together into logical components
        cluster_analysis = self.step_clusters_grouping()

        # Step 2: Generate abstract components from grouped clusters
        analysis = self.step_final_analysis(cluster_analysis)
        # Step 3: Sanitize cluster IDs (remove invalid ones)
        self._sanitize_component_cluster_ids(analysis, cluster_results=cluster_results)
        # Step 4: Assign files to components (deterministic + LLM-based)
        self.classify_files(analysis, cluster_results)
        # Step 5: Fix source code reference lines (resolves reference_file paths for key_entities)
        analysis = self.fix_source_code_reference_lines(analysis)
        # Step 6: Ensure unique key entities across components
        self._ensure_unique_key_entities(analysis)

        return analysis, cluster_results
