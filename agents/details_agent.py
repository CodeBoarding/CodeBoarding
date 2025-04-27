from langchain_core.exceptions import OutputParserException
from langchain_core.output_parsers import PydanticOutputParser
from langchain_core.prompts import PromptTemplate

from agents.agent import CodeBoardingAgent, AnalysisInsights, SubControlFlowGraph, MarkdownOutput
from agents.prompts import SYSTEM_MESSAGE_DETAILS, CFG_DETAILS_MESSAGE, \
    DETAILS_MESSAGE, SUBCFG_DETAILS_MESSAGE, ENHANCE_STRUCTURE_MESSAGE


class DetailsAgent(CodeBoardingAgent):
    def __init__(self, root_dit, project_name):
        super().__init__(root_dit, SYSTEM_MESSAGE_DETAILS)
        self.project_name = project_name

        self.parsers = {
            "cfg": PydanticOutputParser(pydantic_object=AnalysisInsights),
            "structure": PydanticOutputParser(pydantic_object=AnalysisInsights),
            "document": PydanticOutputParser(pydantic_object=MarkdownOutput),
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
            "document": PromptTemplate(template=DETAILS_MESSAGE, input_variables=["insight_so_far", "component"],
                                       partial_variables={
                                           "format_instructions": self.parsers["document"].get_format_instructions()}),
        }
        self.context = {}

    def step_subcfg(self, cfg_str, component):
        print(f"[Details Agent - INFO] Analyzing details on subcfg for {component.name}")
        prompt = self.prompts["subcfg"].format(project_name=self.project_name, cfg_str=cfg_str,
                                               component=component.llm_str())
        response = self._invoke(prompt)
        self.context['subcfg_insight'] = response
        return response

    def step_cfg(self, component):
        print(f"[Details Agent - INFO] Analyzing details on cfg for {component.name}")
        prompt = self.prompts["cfg"].format(project_name=self.project_name,
                                            cfg_str=self.context['subcfg_insight'],
                                            component=component.llm_str())
        response = self._invoke(prompt)
        parsed = self.parsers["cfg"].parse(response)
        self.context['cfg_insight'] = parsed  # Store for next step
        return parsed

    def step_enhance_structure(self, component):
        print(f"[Details Agent - INFO] Analyzing details on structure for {component.name}")
        prompt = self.prompts["structure"].format(
            project_name=self.project_name,
            insight_so_far=self.context.get('cfg_insight').llm_str(),
            component=component.llm_str()
        )
        response = self._invoke(prompt)
        parsed = self.parsers["structure"].parse(response)
        self.context['structure_insight'] = parsed
        return parsed

    def step_markdown(self, component):
        print(f"[Details Agent - INFO] Generating details documentation")
        prompt = self.prompts["document"].format(
            insight_so_far=self.context['structure_insight'].llm_str(),
            component=component.llm_str()
        )
        response = self._invoke(prompt)
        try:
            return self.parsers["document"].parse(response)
        except OutputParserException:
            return response
