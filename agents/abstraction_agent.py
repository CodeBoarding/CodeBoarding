import logging
import os.path
from pathlib import Path

from langchain_core.prompts import PromptTemplate

from agents.agent import LargeModelAgent
from agents.agent_responses import (
    LLMBaseModel,
    AnalysisInsights,
    CFGAnalysisInsights,
    LLMBaseModel,
    ValidationInsights,
    MetaAnalysisInsights,
    ComponentFiles,
    Component,
)
from agents.prompts import (
    get_cfg_message,
    get_source_message,
    get_system_message,
    get_conclusive_analysis_message,
    get_feedback_message,
    get_classification_message,
)
from monitoring import trace
from static_analyzer.analysis_result import StaticAnalysisResults

logger = logging.getLogger(__name__)


class AbstractionAgent(LargeModelAgent):
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
            "cfg": PromptTemplate(
                template=get_cfg_message(), input_variables=["project_name", "cfg_str", "meta_context", "project_type"]
            ),
            "source": PromptTemplate(
                template=get_source_message(), input_variables=["insight_so_far", "meta_context", "project_type"]
            ),
            "final_analysis": PromptTemplate(
                template=get_conclusive_analysis_message(),
                input_variables=["project_name", "cfg_insight", "source_insight", "meta_context", "project_type"],
            ),
            "classification": PromptTemplate(
                template=get_classification_message(), input_variables=["project_name", "components", "files"]
            ),
            "feedback": PromptTemplate(template=get_feedback_message(), input_variables=["analysis", "feedback"]),
        }

    @trace
    def step_cfg(self):
        logger.info(f"[AbstractionAgent] Analyzing CFG for project: {self.project_name}")
        meta_context_str = self.meta_context.llm_str() if self.meta_context else "No project context available."
        project_type = self.meta_context.project_type if self.meta_context else "unknown"

        programming_langs = self.static_analysis.get_languages()
        community_strs = ""
        if len(programming_langs) > 1:
            community_strs += f"This project contains multiple programming languages: {', '.join(programming_langs)}.\n"
        elif len(programming_langs) == 0:
            logger.warning(f"[AbstractionAgent] No programming languages detected for project: {self.project_name}")
            community_strs += "No programming languages detected.\n"

        # Generate visualizations for each language
        for pl in programming_langs:
            cfg = self.static_analysis.get_cfg(pl)
            community_strs += cfg.to_cluster_string()

        prompt = self.prompts["cfg"].format(
            project_name=self.project_name,
            cfg_str=community_strs,
            meta_context=meta_context_str,
            project_type=project_type,
        )
        parsed_response = self._parse_invoke(prompt, CFGAnalysisInsights)
        self.context["cfg_insight"] = parsed_response
        return parsed_response

    @trace
    def step_source(self):
        logger.info(f"[AbstractionAgent] Analyzing Source for project: {self.project_name}")
        insight_str = ""
        for insight_type, analysis_insight in self.context.items():
            insight_str += f"## {insight_type.capitalize()} Insight\n"
            insight_str += analysis_insight.llm_str() + "\n\n"

        meta_context_str = self.meta_context.llm_str() if self.meta_context else "No project context available."
        project_type = self.meta_context.project_type if self.meta_context else "unknown"

        prompt = self.prompts["source"].format(
            insight_so_far=insight_str, meta_context=meta_context_str, project_type=project_type
        )
        parsed_response = self._parse_invoke(prompt, AnalysisInsights)
        self.context["source"] = parsed_response
        return parsed_response

    @trace
    def generate_analysis(self):
        logger.info(f"[AbstractionAgent] Generating final analysis for project: {self.project_name}")
        meta_context_str = self.meta_context.llm_str() if self.meta_context else "No project context available."
        project_type = self.meta_context.project_type if self.meta_context else "unknown"

        cfg_insight = self.context.get("cfg_insight")
        cfg_insight_str = (
            cfg_insight.llm_str() if isinstance(cfg_insight, AnalysisInsights) else "No CFG insight available."
        )
        source_insight = self.context.get("source")
        source_insight_str = (
            source_insight.llm_str() if isinstance(source_insight, AnalysisInsights) else "No source insight available."
        )

        prompt = self.prompts["final_analysis"].format(
            project_name=self.project_name,
            cfg_insight=cfg_insight_str,
            source_insight=source_insight_str,
            meta_context=meta_context_str,
            project_type=project_type,
        )
        return self._parse_invoke(prompt, AnalysisInsights)

    @trace
    def apply_feedback(self, analysis: AnalysisInsights, feedback: ValidationInsights):
        """
        Apply feedback to the analysis and return the updated analysis.
        This method should modify the analysis based on the feedback provided.
        """
        logger.info(f"[AbstractionAgent] Applying feedback to analysis for project: {self.project_name}")
        prompt = self.prompts["feedback"].format(analysis=analysis.llm_str(), feedback=feedback.llm_str())
        analysis = self._parse_invoke(prompt, AnalysisInsights)
        return self.fix_source_code_reference_lines(analysis)

    @trace
    def classify_files(self, analysis: AnalysisInsights):
        """
        Classify files into components based on the analysis. It will modify directly the analysis object.
        This method assigns files to components based on their relevance.
        It returns a list of ComponentFiles indicating which files belong to which components.
        It also adds an "Unclassified" component for files that do not fit into any other component.
        """
        logger.info(f"[AbstractionAgent] Classifying files for project: {self.project_name}")
        all_files = self.static_analysis.get_all_source_files()
        analysis.components.append(
            Component(
                name="Unclassified",
                description="Component for all unclassified files and utility functions (Utility functions/External Libraries/Dependencies)",
                referenced_source_code=[],
            )
        )
        component_str = "\n".join([component.llm_str() for component in analysis.components])

        for comp in analysis.components:
            comp.assigned_files = []

        files = []
        for i in range(0, len(all_files), 100):
            file_block = [str(f) for f in all_files[i : i + 100]]
            prompt = self.prompts["classification"].format(
                project_name=self.project_name, components=component_str, files="\n".join(file_block)
            )
            classification = self._parse_invoke(prompt, ComponentFiles)
            files.extend(classification.file_paths)
        for file in files:
            component_match: Component | None = next(
                (c for c in analysis.components if c.name == file.component_name), None
            )
            if component_match is None:
                logger.warning(f"[AbstractionAgent] File {file.component_name} not found in analysis")
                continue
            component_match.assigned_files.append(file.file_path)

        for comp in analysis.components:
            files = []
            for file in comp.assigned_files:
                if os.path.exists(file):
                    # relative path from the repo root
                    rel_file = os.path.relpath(file, self.repo_dir)
                    files.append(rel_file)
                else:
                    files.append(file)
            comp.assigned_files = files

    def run(self):
        self.step_cfg()
        self.step_source()
        analysis = self.generate_analysis()
        analysis = self.fix_source_code_reference_lines(analysis)
        return analysis
