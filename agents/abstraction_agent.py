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
    get_cluster_analysis_message,
    get_final_analysis_message,
    get_feedback_message,
)
from agents.cluster_methods_mixin import ClusterMethodsMixin
from monitoring import trace
from static_analyzer.analysis_result import StaticAnalysisResults

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
            "analyze_clusters": PromptTemplate(
                template=get_cluster_analysis_message(),
                input_variables=["project_name", "cfg_clusters", "meta_context", "project_type"],
            ),
            "final_analysis": PromptTemplate(
                template=get_final_analysis_message(),
                input_variables=["project_name", "cluster_analysis", "meta_context", "project_type"],
            ),
            "feedback": PromptTemplate(template=get_feedback_message(), input_variables=["analysis", "feedback"]),
        }

    @trace
    def analyze_clusters(self) -> ClusterAnalysis:
        logger.info(f"[AbstractionAgent] Analyzing CFG clusters for: {self.project_name}")

        meta_context_str = self.meta_context.llm_str() if self.meta_context else "No project context available."
        project_type = self.meta_context.project_type if self.meta_context else "unknown"

        programming_langs = self.static_analysis.get_languages()

        # Build cluster string that explicitly shows cluster IDs
        cluster_str = self._build_cluster_string(programming_langs)

        prompt = self.prompts["analyze_clusters"].format(
            project_name=self.project_name,
            cfg_clusters=cluster_str,
            meta_context=meta_context_str,
            project_type=project_type,
        )

        cluster_analysis = self._parse_invoke(prompt, ClusterAnalysis)
        return cluster_analysis

    @trace
    def generate_analysis(self, cluster_analysis: ClusterAnalysis) -> AnalysisInsights:
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
        # Step 1: Run static analysis to get initial clusters
        cluster_analysis = self.analyze_clusters()

        # Step 2: Generate abstract components by grouping clusters
        # (LLM groups many clusters into fewer abstract ones with source_cluster_ids)
        analysis = self.generate_analysis(cluster_analysis)

        # Step 3: Validate that invalid cluster IDs are removed
        self._validate_cluster_ids(analysis)

        # Step 4: Assign files to components based on source_cluster_ids
        self.classify_files(analysis)

        # Step 5: Fix source code reference lines (resolves reference_file paths for key_entities)
        analysis = self.fix_source_code_reference_lines(analysis)

        # Step 6: Ensure unique key entities across components
        self._ensure_unique_key_entities(analysis)

        return analysis
