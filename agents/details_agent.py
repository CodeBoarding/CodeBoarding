import logging
from pathlib import Path

from langchain_core.prompts import PromptTemplate

from agents.agent import CodeBoardingAgent
from agents.agent_responses import AnalysisInsights, CFGAnalysisInsights, ValidationInsights, Component, \
    MetaAnalysisInsights
from agents.prompts import SYSTEM_DETAILS_MESSAGE, CFG_DETAILS_MESSAGE, \
    DETAILS_MESSAGE, SUBCFG_DETAILS_MESSAGE, ENHANCE_STRUCTURE_MESSAGE, FEEDBACK_MESSAGE
from static_analyzer.analysis_result import StaticAnalysisResults

logger = logging.getLogger(__name__)


class DetailsAgent(CodeBoardingAgent):
    def __init__(self, repo_dir: Path, static_analysis: StaticAnalysisResults, project_name: str,
                 meta_context: MetaAnalysisInsights):
        super().__init__(repo_dir, static_analysis, SYSTEM_DETAILS_MESSAGE)
        self.project_name = project_name
        self.meta_context = meta_context

        self.prompts = {
            "subcfg": PromptTemplate(template=SUBCFG_DETAILS_MESSAGE,
                                     input_variables=["project_name", "cfg_str", "component"]),
            "cfg": PromptTemplate(template=CFG_DETAILS_MESSAGE,
                                  input_variables=["cfg_str", "project_name", "meta_context", "project_type"]),
            "structure": PromptTemplate(template=ENHANCE_STRUCTURE_MESSAGE,
                                        input_variables=["insight_so_far", "component", "project_name", "meta_context",
                                                         "project_type"]),
            "final_analysis": PromptTemplate(template=DETAILS_MESSAGE,
                                             input_variables=["insight_so_far", "component", "meta_context",
                                                              "project_type"]),
            "feedback": PromptTemplate(template=FEEDBACK_MESSAGE, input_variables=["analysis", "feedback"]),
        }

        self.context = {}

    def step_subcfg(self, component: Component):
        logger.info(f"[DetailsAgent] Analyzing details on subcfg for {component.name}")
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
        logger.info(f"[DetailsAgent] Generating details documentation")
        meta_context_str = self.meta_context.llm_str() if self.meta_context else "No project context available."
        project_type = self.meta_context.project_type if self.meta_context else "unknown"

        prompt = self.prompts["final_analysis"].format(
            insight_so_far=self.context['structure_insight'].llm_str(),
            component=component.llm_str(),
            meta_context=meta_context_str,
            project_type=project_type
        )
        analysis = self._parse_invoke(prompt, AnalysisInsights)
        return self.fix_source_code_reference_lines(analysis)

    def apply_feedback(self, analysis: AnalysisInsights, feedback: ValidationInsights):
        """
        Apply feedback to the analysis and return the updated analysis.
        This method should modify the analysis based on the feedback provided.
        """
        logger.info(f"[DetailsAgent] Applying feedback to analysis for project: {self.project_name}")
        prompt = self.prompts["feedback"].format(analysis=analysis.llm_str(), feedback=feedback.llm_str())
        analysis = self._parse_invoke(prompt, AnalysisInsights)
        return self.fix_source_code_reference_lines(analysis)

    def run(self, cfg_str: str, component: Component):
        """
        Run the details analysis for the given component.
        This method should execute the steps in order and return the final analysis.
        """
        self.step_subcfg(cfg_str, component)
        self.step_cfg(component)
        self.step_enhance_structure(component)
        analysis = self.step_analysis(component)

        return analysis
