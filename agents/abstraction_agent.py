import logging

from langchain.prompts import PromptTemplate
from langchain_core.output_parsers import PydanticOutputParser

from agents.agent import CodeBoardingAgent
from agents.agent_responses import AnalysisInsights, CFGAnalysisInsights
from agents.prompts import CFG_MESSAGE, SOURCE_MESSAGE, SYSTEM_MESSAGE, CONCLUSIVE_ANALYSIS_MESSAGE


class AbstractionAgent(CodeBoardingAgent):
    def __init__(self, repo_dir, output_dir, cfg, project_name):
        super().__init__(repo_dir, output_dir, cfg, SYSTEM_MESSAGE)

        self.project_name = project_name

        self.context = {"structure_insight": []}  # Store evolving insights here

        # Define your prompts for each stage, and their parsers
        self.parsers = {
            "cfg": PydanticOutputParser(pydantic_object=CFGAnalysisInsights),
            "source": PydanticOutputParser(pydantic_object=AnalysisInsights),
            "final_analysis": PydanticOutputParser(pydantic_object=AnalysisInsights),
        }

        self.prompts = {
            "cfg": PromptTemplate(template=CFG_MESSAGE, input_variables=["project_name", "cfg_str"],
                                  partial_variables={
                                      "format_instructions": self.parsers["cfg"].get_format_instructions()}),
            "source": PromptTemplate(template=SOURCE_MESSAGE, input_variables=["insight_so_far"],
                                     partial_variables={
                                         "format_instructions": self.parsers["source"].get_format_instructions()}),
            "final_analysis": PromptTemplate(template=CONCLUSIVE_ANALYSIS_MESSAGE,
                                             input_variables=["project_name", "cfg_insight", "structure_insight",
                                                              "source_insight"],
                                             partial_variables={"format_instructions": self.parsers[
                                                 "final_analysis"].get_format_instructions()})
        }

    def step_cfg(self, cfg_str):
        logging.info(f"[AbstractionAgent] Analyzing CFG for project: {self.project_name}")
        prompt = self.prompts["cfg"].format(project_name=self.project_name, cfg_str=cfg_str)
        parsed_response = self._parse_invoke(prompt, self.parsers["cfg"])
        self.context['cfg_insight'] = parsed_response
        return parsed_response

    def step_source(self):
        logging.info(f"[AbstractionAgent] Analyzing Source for project: {self.project_name}")
        insight_str = ""
        for insight_type, analysis_insight in self.context.items():
            insight_str += f"## {insight_type.capitalize()} Insight\n"
            if type(analysis_insight) is list:
                insight_str += "\n".join([f"- {insight.llm_str()}" for insight in analysis_insight]) + "\n\n"
            else:
                insight_str += analysis_insight.llm_str() + "\n\n"

        prompt = self.prompts["source"].format(
            insight_so_far=insight_str,
        )
        parsed_response = self._parse_invoke(prompt, self.parsers["source"])
        self.context["source"] = parsed_response
        return parsed_response

    def generate_analysis(self):
        logging.info(f"[AbstractionAgent] Generating final analysis for project: {self.project_name}")
        prompt = self.prompts["final_analysis"].format(
            project_name=self.project_name,
            cfg_insight=self.context.get('cfg_insight').llm_str(),
            source_insight=self.context.get('source').llm_str()
        )
        analysis_result = self._parse_invoke(prompt, self.parsers["final_analysis"])
        return self.fix_source_code_reference_lines(analysis_result)
