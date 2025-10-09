import logging
import os
from pathlib import Path

from langchain_core.prompts import PromptTemplate

from agents.agent import CodeBoardingAgent
from agents.agent_responses import AnalysisInsights, CFGAnalysisInsights, ValidationInsights, Component, \
    MetaAnalysisInsights, ComponentFiles
from agents.prompts import (
    get_system_details_message, get_cfg_details_message, get_details_message,
    get_subcfg_details_message, get_enhance_structure_message, get_feedback_message, get_classification_message
)
from static_analyzer.analysis_result import StaticAnalysisResults

logger = logging.getLogger(__name__)


class DetailsAgent(CodeBoardingAgent):
    def __init__(self, repo_dir: Path, static_analysis: StaticAnalysisResults, project_name: str,
                 meta_context: MetaAnalysisInsights):
        super().__init__(repo_dir, static_analysis, get_system_details_message())
        self.project_name = project_name
        self.meta_context = meta_context

        self.prompts = {
            "subcfg": PromptTemplate(template=get_subcfg_details_message(),
                                     input_variables=["project_name", "cfg_str", "component"]),
            "cfg": PromptTemplate(template=get_cfg_details_message(),
                                  input_variables=["cfg_str", "project_name", "meta_context", "project_type"]),
            "structure": PromptTemplate(template=get_enhance_structure_message(),
                                        input_variables=["insight_so_far", "component", "project_name", "meta_context",
                                                         "project_type"]),
            "final_analysis": PromptTemplate(template=get_details_message(),
                                             input_variables=["insight_so_far", "component", "meta_context",
                                                              "project_type"]),
            "feedback": PromptTemplate(template=get_feedback_message(), input_variables=["analysis", "feedback"]),
            "classification": PromptTemplate(template=get_classification_message(),
                                             input_variables=["project_name", "components", "files"]),
        }

        self.context = {}

    def step_subcfg(self, component: Component):
        logger.info(f"[DetailsAgent] Analyzing details on subcfg for {component.name}")
        # Now lets filter the cfg:
        self.context['subcfg_insight'] = self.read_cfg_tool.component_cfg(component)

    def step_cfg(self, component: Component):
        logger.info(f"[DetailsAgent] Analyzing details on cfg for {component.name}")
        meta_context_str = self.meta_context.llm_str() if self.meta_context else "No project context available."
        project_type = self.meta_context.project_type if self.meta_context else "unknown"

        prompt = self.prompts["cfg"].format(
            project_name=self.project_name,
            cfg_str=self.context['subcfg_insight'],
            component=component.llm_str(),
            meta_context=meta_context_str,
            project_type=project_type
        )
        parsed = self._parse_invoke(prompt, CFGAnalysisInsights)
        self.context['cfg_insight'] = parsed  # Store for next step
        return parsed

    def step_enhance_structure(self, component: Component):
        logger.info(f"[DetailsAgent] Analyzing details on structure for {component.name}")
        meta_context_str = self.meta_context.llm_str() if self.meta_context else "No project context available."
        project_type = self.meta_context.project_type if self.meta_context else "unknown"

        prompt = self.prompts["structure"].format(
            project_name=self.project_name,
            insight_so_far=self.context.get('cfg_insight').llm_str(),
            component=component.llm_str(),
            meta_context=meta_context_str,
            project_type=project_type
        )
        parsed = self._parse_invoke(prompt, AnalysisInsights)
        self.context['structure_insight'] = parsed
        return parsed

    def step_analysis(self, component: Component):
        logger.info("[DetailsAgent] Generating details documentation")
        meta_context_str = self.meta_context.llm_str() if self.meta_context else "No project context available."
        project_type = self.meta_context.project_type if self.meta_context else "unknown"

        prompt = self.prompts["final_analysis"].format(
            insight_so_far=self.context['structure_insight'].llm_str(),
            component=component.llm_str(),
            meta_context=meta_context_str,
            project_type=project_type
        )
        return self._parse_invoke(prompt, AnalysisInsights)

    def apply_feedback(self, analysis: AnalysisInsights, feedback: ValidationInsights):
        """
        Apply feedback to the analysis and return the updated analysis.
        This method should modify the analysis based on the feedback provided.
        """
        logger.info(f"[DetailsAgent] Applying feedback to analysis for project: {self.project_name}")
        prompt = self.prompts["feedback"].format(analysis=analysis.llm_str(), feedback=feedback.llm_str())
        analysis = self._parse_invoke(prompt, AnalysisInsights)
        return self.fix_source_code_reference_lines(analysis)

    def run(self, component: Component):
        """
        Run the details analysis for the given component.
        This method should execute the steps in order and return the final analysis.
        """
        logger.info(f"Processing component: {component.name}")
        self.step_subcfg(component)
        self.step_cfg(component)
        self.step_enhance_structure(component)
        analysis = self.step_analysis(component)
        return self.fix_source_code_reference_lines(analysis)

    def classify_files(self, component: Component, analysis: AnalysisInsights):
        """
        Classify the component using the LLM.
        This method should return a string representing the classification.
        """
        logger.info(f"[DetailsAgent] Classifying component {component.name} based on assigned files")
        all_files = component.assigned_files
        analysis.components.append(Component(name="Unclassified",
                                             description="Component for all unclassified files and utility functions (Utility functions/External Libraries/Dependencies)",
                                             referenced_source_code=[]))
        component_str = "\n".join([component.llm_str() for component in analysis.components])

        for comp in analysis.components:
            comp.assigned_files = []

        files = []
        for i in range(0, len(all_files), 100):
            file_block = [str(f) for f in all_files[i:i + 100]]
            prompt = self.prompts["classification"].format(project_name=self.project_name, components=component_str,
                                                           files="\n".join(file_block))
            classification = self._parse_invoke(prompt, ComponentFiles)
            files.extend(classification.file_paths)
        for file in files:
            comp = next((c for c in analysis.components if c.name == file.component_name), None)
            if comp is None:
                logger.warning(f"[DetailsAgent] File {file.component_name} not found in analysis")
                continue
            comp.assigned_files.append(file.file_path)

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
