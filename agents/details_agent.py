from langchain_core.output_parsers import PydanticOutputParser
from langchain_core.prompts import PromptTemplate

from agents.agent import CodeBoardingAgent, AnalysisInsights
from agents.prompts import SYSTEM_MESSAGE_DETAILS, CFG_DETAILS_MESSAGE, STRUCTURE_DETAILS_MESSAGE, \
    DETAILS_MESSAGE


class DetailsAgent(CodeBoardingAgent):
    def __init__(self, root_dit, project_name):
        super().__init__(root_dit, SYSTEM_MESSAGE_DETAILS)
        self.project_name = project_name

        self.parsers = {
            "cfg": PydanticOutputParser(pydantic_object=AnalysisInsights),
            "structure": PydanticOutputParser(pydantic_object=AnalysisInsights),
        }

        self.prompts = {
            "cfg": PromptTemplate(template=CFG_DETAILS_MESSAGE,
                                  input_variables=["project_name", "cfg_str", "component"],
                                  partial_variables={
                                      "format_instructions": self.parsers["cfg"].get_format_instructions()}),
            "structure": PromptTemplate(template=STRUCTURE_DETAILS_MESSAGE,
                                        input_variables=["cfg_insight", "structure_graph", "component", "project_name"],
                                        partial_variables={"format_instructions": self.parsers[
                                            "structure"].get_format_instructions()}),
            "source": PromptTemplate(template=DETAILS_MESSAGE, input_variables=["insight_so_far"]),
        }
        self.context = {"structure_insight": []}

    def step_cfg(self, cfg_str, component):
        print(f"[INFO] Analyzing details on cfg for {component.name}")
        prompt = self.prompts["cfg"].format(project_name=self.project_name, cfg_str=cfg_str,
                                            component=component.llm_str())
        response = self._invoke(prompt)
        parsed = self.parsers["cfg"].parse(response)
        self.context['cfg_insight'] = parsed  # Store for next step
        return parsed

    def step_structure(self, structure_graph, component):
        print(f"[INFO] Analyzing details on structure for {component.name}")
        prompt = self.prompts["structure"].format(
            project_name=self.project_name,
            cfg_insight=self.context.get('cfg_insight').llm_str(),
            structure_graph=structure_graph,
            component=component.llm_str()
        )
        response = self._invoke(prompt)
        parsed = self.parsers["structure"].parse(response)
        self.context['structure_insight'].append(parsed)
        return parsed

    def step_source(self):
        print(f"[INFO] Analyzing details on source code")
        insight_str = ""
        for insight_type, anaylsys_insight in self.context.items():
            insight_str += f"## {insight_type.capitalize()} Insight\n"
            if type(anaylsys_insight) is list:
                insight_str += "\n".join([f"- {insight.llm_str()}" for insight in anaylsys_insight]) + "\n\n"
            else:
                insight_str += anaylsys_insight.llm_str() + "\n\n"
        prompt = self.prompts["source"].format(insight_so_far=insight_str)
        response = self._invoke(prompt)
        return response