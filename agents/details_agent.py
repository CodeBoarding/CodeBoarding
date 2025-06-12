import logging

from langchain_core.output_parsers import PydanticOutputParser
from langchain_core.prompts import PromptTemplate

from agents.agent import CodeBoardingAgent
from agents.agent_responses import AnalysisInsights, CFGAnalysisInsights
from agents.prompts import SYSTEM_DETAILS_MESSAGE, CFG_DETAILS_MESSAGE, \
    DETAILS_MESSAGE, SUBCFG_DETAILS_MESSAGE, ENHANCE_STRUCTURE_MESSAGE


class DetailsAgent(CodeBoardingAgent):
    def __init__(self, repo_dir, output_dir, cfg, project_name):
        super().__init__(repo_dir, output_dir, cfg, SYSTEM_DETAILS_MESSAGE)
        self.project_name = project_name

        self.parsers = {
            "cfg": PydanticOutputParser(pydantic_object=CFGAnalysisInsights),
            "structure": PydanticOutputParser(pydantic_object=AnalysisInsights),
            "final_analysis": PydanticOutputParser(pydantic_object=AnalysisInsights),
        }

        self.prompts = {
            "subcfg": PromptTemplate(template=SUBCFG_DETAILS_MESSAGE,
                                     input_variables=["project_name", "cfg_str", "component"]),
            "cfg": PromptTemplate(template=CFG_DETAILS_MESSAGE,
                                  input_variables=["cfg_str", "project_name"],
                                  partial_variables={
                                      "format_instructions": self.parsers["cfg"].get_format_instructions()}),
            "structure": PromptTemplate(template=ENHANCE_STRUCTURE_MESSAGE,
                                        input_variables=["insight_so_far", "component", "project_name"],
                                        partial_variables={
                                            "format_instructions": self.parsers[
                                                "structure"].get_format_instructions()}),
            "final_analysis": PromptTemplate(template=DETAILS_MESSAGE, input_variables=["insight_so_far", "component"],
                                             partial_variables={
                                                 "format_instructions": self.parsers[
                                                     "final_analysis"].get_format_instructions()}),
        }
        self.context = {}

    def step_subcfg(self, cfg_str, component):
        logging.info(f"[DetailsAgent] Analyzing details on subcfg for {component.name}")
        prompt = self.prompts["subcfg"].format(project_name=self.project_name, cfg_str=cfg_str,
                                               component=component.llm_str())
        response = self._invoke(prompt)
        self.context['subcfg_insight'] = response

    def step_cfg(self, component):
        logging.info(f"[DetailsAgent] Analyzing details on cfg for {component.name}")
        prompt = self.prompts["cfg"].format(project_name=self.project_name,
                                            cfg_str=self.context['subcfg_insight'],
                                            component=component.llm_str())
        parsed = self._parse_invoke(prompt, self.parsers["cfg"])
        self.context['cfg_insight'] = parsed  # Store for next step
        return parsed

    def step_enhance_structure(self, component):
        logging.info(f"[DetailsAgent] Analyzing details on structure for {component.name}")
        prompt = self.prompts["structure"].format(
            project_name=self.project_name,
            insight_so_far=self.context.get('cfg_insight').llm_str(),
            component=component.llm_str()
        )
        parsed = self._parse_invoke(prompt, self.parsers["structure"])
        self.context['structure_insight'] = parsed
        return parsed

    def step_analysis(self, component):
        logging.info(f"[DetailsAgent] Generating details documentation")
        prompt = self.prompts["final_analysis"].format(
            insight_so_far=self.context['structure_insight'].llm_str(),
            component=component.llm_str(),
        )
        analysis = self._parse_invoke(prompt, self.parsers["final_analysis"])
        return self.fix_source_code_reference_lines(analysis)
