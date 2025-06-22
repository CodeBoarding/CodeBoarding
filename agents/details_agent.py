import logging

from langchain_core.prompts import PromptTemplate

from agents.agent import CodeBoardingAgent
from agents.agent_responses import AnalysisInsights, CFGAnalysisInsights, ValidationInsights, Component
from agents.prompts import SYSTEM_DETAILS_MESSAGE, CFG_DETAILS_MESSAGE, \
    DETAILS_MESSAGE, SUBCFG_DETAILS_MESSAGE, ENHANCE_STRUCTURE_MESSAGE, FEEDBACK_MESSAGE


class DetailsAgent(CodeBoardingAgent):
    def __init__(self, repo_dir, output_dir, cfg, project_name):
        super().__init__(repo_dir, output_dir, cfg, SYSTEM_DETAILS_MESSAGE)
        self.project_name = project_name

        self.prompts = {
            "subcfg": PromptTemplate(template=SUBCFG_DETAILS_MESSAGE,
                                     input_variables=["project_name", "cfg_str", "component"]),
            "cfg": PromptTemplate(template=CFG_DETAILS_MESSAGE, input_variables=["cfg_str", "project_name"]),
            "structure": PromptTemplate(template=ENHANCE_STRUCTURE_MESSAGE,
                                        input_variables=["insight_so_far", "component", "project_name"]),
            "final_analysis": PromptTemplate(template=DETAILS_MESSAGE, input_variables=["insight_so_far", "component"]),
            "feedback": PromptTemplate(template=FEEDBACK_MESSAGE, input_variables=["analysis", "feedback"]),
        }

        self.context = {}

    def step_subcfg(self, cfg_str: str, component: Component):
        logging.info(f"[DetailsAgent] Analyzing details on subcfg for {component.name}")
        self.context['subcfg_insight'] = self.read_cfg_tool.component_cfg(component)

    def step_cfg(self, component: Component):
        logging.info(f"[DetailsAgent] Analyzing details on cfg for {component.name}")
        prompt = self.prompts["cfg"].format(project_name=self.project_name,
                                            cfg_str=self.context['subcfg_insight'],
                                            component=component.llm_str())
        parsed = self._parse_invoke(prompt, CFGAnalysisInsights)
        self.context['cfg_insight'] = parsed  # Store for next step
        return parsed

    def step_enhance_structure(self, component: Component):
        logging.info(f"[DetailsAgent] Analyzing details on structure for {component.name}")
        prompt = self.prompts["structure"].format(
            project_name=self.project_name,
            insight_so_far=self.context.get('cfg_insight').llm_str(),
            component=component.llm_str()
        )
        parsed = self._parse_invoke(prompt, AnalysisInsights)
        self.context['structure_insight'] = parsed
        return parsed

    def step_analysis(self, component: Component):
        logging.info(f"[DetailsAgent] Generating details documentation")
        prompt = self.prompts["final_analysis"].format(
            insight_so_far=self.context['structure_insight'].llm_str(),
            component=component.llm_str(),
        )
        analysis = self._parse_invoke(prompt, AnalysisInsights)
        return self.fix_source_code_reference_lines(analysis)

    def apply_feedback(self, analysis: AnalysisInsights, feedback: ValidationInsights):
        """
        Apply feedback to the analysis and return the updated analysis.
        This method should modify the analysis based on the feedback provided.
        """
        logging.info(f"[DetailsAgent] Applying feedback to analysis for project: {self.project_name}")
        prompt = self.prompts["feedback"].format(analysis=analysis.llm_str(), feedback=feedback)
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
