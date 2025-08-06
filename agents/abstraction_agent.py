import logging

from langchain.prompts import PromptTemplate

from agents.agent import CodeBoardingAgent
from agents.agent_responses import AnalysisInsights, CFGAnalysisInsights, ValidationInsights
from agents.prompts import CFG_MESSAGE, SOURCE_MESSAGE, SYSTEM_MESSAGE, CONCLUSIVE_ANALYSIS_MESSAGE, FEEDBACK_MESSAGE

logger = logging.getLogger(__name__)


class AbstractionAgent(CodeBoardingAgent):
    def __init__(self, repo_dir, static_analysis, project_name, meta_context):
        super().__init__(repo_dir, static_analysis, SYSTEM_MESSAGE)

        self.project_name = project_name
        self.meta_context = meta_context

        self.context = {"structure_insight": []}  # Store evolving insights here

        self.prompts = {
            "cfg": PromptTemplate(template=CFG_MESSAGE,
                                  input_variables=["project_name", "cfg_str", "meta_context", "project_type"]),
            "source": PromptTemplate(template=SOURCE_MESSAGE,
                                     input_variables=["insight_so_far", "meta_context", "project_type"]),
            "final_analysis": PromptTemplate(template=CONCLUSIVE_ANALYSIS_MESSAGE,
                                             input_variables=["project_name", "cfg_insight", "source_insight",
                                                              "meta_context", "project_type"]),
            "feedback": PromptTemplate(template=FEEDBACK_MESSAGE, input_variables=["analysis", "feedback"])
        }

    def step_cfg(self):
        logger.info(f"[AbstractionAgent] Analyzing CFG for project: {self.project_name}")
        meta_context_str = self.meta_context.llm_str() if self.meta_context else "No project context available."
        project_type = self.meta_context.project_type if self.meta_context else "unknown"

        prompt = self.prompts["cfg"].format(
            project_name=self.project_name,
            cfg_str=str(self.read_cfg_tool._run()),
            meta_context=meta_context_str,
            project_type=project_type
        )
        parsed_response = self._parse_invoke(prompt, CFGAnalysisInsights)
        self.context['cfg_insight'] = parsed_response
        return parsed_response

    def step_source(self):
        logger.info(f"[AbstractionAgent] Analyzing Source for project: {self.project_name}")
        insight_str = ""
        for insight_type, analysis_insight in self.context.items():
            insight_str += f"## {insight_type.capitalize()} Insight\n"
            if type(analysis_insight) is list:
                insight_str += "\n".join([f"- {insight.llm_str()}" for insight in analysis_insight]) + "\n\n"
            else:
                insight_str += analysis_insight.llm_str() + "\n\n"

        meta_context_str = self.meta_context.llm_str() if self.meta_context else "No project context available."
        project_type = self.meta_context.project_type if self.meta_context else "unknown"

        prompt = self.prompts["source"].format(
            insight_so_far=insight_str,
            meta_context=meta_context_str,
            project_type=project_type
        )
        parsed_response = self._parse_invoke(prompt, AnalysisInsights)
        self.context["source"] = parsed_response
        return parsed_response

    def generate_analysis(self):
        logger.info(f"[AbstractionAgent] Generating final analysis for project: {self.project_name}")
        meta_context_str = self.meta_context.llm_str() if self.meta_context else "No project context available."
        project_type = self.meta_context.project_type if self.meta_context else "unknown"

        prompt = self.prompts["final_analysis"].format(
            project_name=self.project_name,
            cfg_insight=self.context.get('cfg_insight').llm_str(),
            source_insight=self.context.get('source').llm_str(),
            meta_context=meta_context_str,
            project_type=project_type
        )
        analysis_result = self._parse_invoke(prompt, AnalysisInsights)
        return self.fix_source_code_reference_lines(analysis_result)

    def apply_feedback(self, analysis: AnalysisInsights, feedback: ValidationInsights):
        """
        Apply feedback to the analysis and return the updated analysis.
        This method should modify the analysis based on the feedback provided.
        """
        logger.info(f"[AbstractionAgent] Applying feedback to analysis for project: {self.project_name}")
        prompt = self.prompts["feedback"].format(analysis=analysis.llm_str(), feedback=feedback.llm_str())
        analysis = self._parse_invoke(prompt, AnalysisInsights)
        return self.fix_source_code_reference_lines(analysis)

    def run(self):
        self.step_cfg()
        self.step_source()
        return self.generate_analysis()
