import logging
import os.path
from pathlib import Path

from langchain_core.prompts import PromptTemplate

from agents.agent import LargeModelAgent
from agents.agent_responses import (
    LLMBaseModel,
    AnalysisInsights,
    ClusterAnalysis,
    ValidationInsights,
    MetaAnalysisInsights,
    Component,
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

        self.context: dict[str, LLMBaseModel] = {}

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
        self.context["cluster_analysis"] = cluster_analysis
        return cluster_analysis

    @trace
    def generate_analysis(self) -> AnalysisInsights:
        logger.info(f"[AbstractionAgent] Generating final analysis for: {self.project_name}")

        meta_context_str = self.meta_context.llm_str() if self.meta_context else "No project context available."
        project_type = self.meta_context.project_type if self.meta_context else "unknown"

        cluster_analysis = self.context.get("cluster_analysis")
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

    @trace
    def classify_files(self, analysis: AnalysisInsights):
        """
        Classify files into components based on the analysis (DETERMINISTIC).
        It will modify directly the analysis object.

        This method assigns files to components based on:
        1. key_entities references (if file contains those classes/methods)
        2. source_cluster_ids (if file contains nodes from those clusters)
        """
        logger.info(f"[AbstractionAgent] Classifying files deterministically for: {self.project_name}")
        all_files = self.static_analysis.get_all_source_files()

        # Add "Unclassified" component for files that don't fit
        analysis.components.append(
            Component(
                name="Unclassified",
                description="Component for all unclassified files and utility functions (Utility functions/External Libraries/Dependencies)",
                key_entities=[],
                source_cluster_ids=[],
            )
        )

        for comp in analysis.components:
            comp.assigned_files = []

        file_to_clusters = self._build_file_cluster_mapping()
        for file_path in all_files:
            matched_components = self._match_file_to_components(file_path, analysis.components, file_to_clusters)

            if matched_components:
                for component in matched_components:
                    component.assigned_files.append(file_path)
            else:
                unclassified = next(c for c in analysis.components if c.name == "Unclassified")
                unclassified.assigned_files.append(file_path)

        # Convert to relative paths
        for comp in analysis.components:
            normalized_files: list[str] = []
            for file_path in comp.assigned_files:
                if os.path.exists(file_path):
                    rel_file = os.path.relpath(file_path, self.repo_dir)
                    normalized_files.append(rel_file)
                else:
                    normalized_files.append(file_path)
            comp.assigned_files = normalized_files

        logger.info(f"[AbstractionAgent] Classified {len(all_files)} files into {len(analysis.components)} components")

    def run(self):
        self.analyze_clusters()  # Step 1: Understand clusters
        analysis = self.generate_analysis()  # Step 2: Create final components
        analysis = self.fix_source_code_reference_lines(analysis)
        self._ensure_unique_key_entities(analysis)  # Step 3: Ensure key_entities are unique
        return analysis
