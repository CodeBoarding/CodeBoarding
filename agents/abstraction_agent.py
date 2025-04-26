from langchain.prompts import PromptTemplate
from langchain_core.output_parsers import PydanticOutputParser

from agents.agent import CodeBoardingAgent, AnalysisInsights
from agents.prompts import CFG_MESSAGE, STRUCTURE_MESSAGE, SOURCE_MESSAGE, SYSTEM_MESSAGE, MARKDOWN_MESSAGE


class AbstractionAgent(CodeBoardingAgent):
    def __init__(self, root_dir, project_name):
        super().__init__(root_dir, SYSTEM_MESSAGE)

        self.root_dir = root_dir
        self.project_name = project_name

        self.context = {"structure_insight": []}  # Store evolving insights here

        # Define your prompts for each stage, and their parsers
        self.parsers = {
            "cfg": PydanticOutputParser(pydantic_object=AnalysisInsights),
            "structure": PydanticOutputParser(pydantic_object=AnalysisInsights),
            "source": PydanticOutputParser(pydantic_object=AnalysisInsights),
        }

        self.prompts = {
            "cfg": PromptTemplate(template=CFG_MESSAGE, input_variables=["project_name", "cfg_str"],
                                  partial_variables={
                                      "format_instructions": self.parsers["cfg"].get_format_instructions()}),
            "structure": PromptTemplate(template=STRUCTURE_MESSAGE, input_variables=["cfg_insight", "structure_graph"],
                                        partial_variables={"format_instructions": self.parsers[
                                            "structure"].get_format_instructions()}),
            "source": PromptTemplate(template=SOURCE_MESSAGE, input_variables=["insight_so_far"],
                                     partial_variables={
                                         "format_instructions": self.parsers["source"].get_format_instructions()}),
            "markdown": PromptTemplate(template=MARKDOWN_MESSAGE,
                                       input_variables=["project_name", "cfg_insight", "structure_insight",
                                                        "source_insight"]),
        }

    def step_cfg(self, cfg_str):
        print(f"[INFO] Analyzing CFG for project: {self.project_name}")
        prompt = self.prompts["cfg"].format(project_name=self.project_name, cfg_str=cfg_str)
        response = self._invoke(prompt)
        parsed = self.parsers["cfg"].parse(response)
        self.context['cfg_insight'] = parsed  # Store for next step
        return parsed

    def step_packages(self, packages):
        print(f"[INFO] Analyzing Packages for project: {self.project_name}")
        insight_str = ""
        for pkg in packages:
            insight_str += f"- `{pkg}`\n"
        insight_str += "\n"
        self.context['packages'] = insight_str

    def step_structure(self, structure_graph):
        print(f"[INFO] Analyzing Structure for project: {self.project_name}")
        prompt = self.prompts["structure"].format(
            cfg_insight=self.context.get('cfg_insight').llm_str(),
            structure_graph=structure_graph
        )
        response = self._invoke(prompt)
        parsed = self.parsers["structure"].parse(response)
        self.context['structure_insight'].append(parsed)
        return parsed

    def step_source(self):
        print(f"[INFO] Analyzing Source for project: {self.project_name}")
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
        response = self._invoke(prompt)
        self.context["source"] = self.parsers["source"].parse(response)
        return self.context["source"]

    def generate_markdown(self):
        print(f"[INFO] Generating markdown for project: {self.project_name}")
        prompt = self.prompts["markdown"].format(
            project_name=self.project_name,
            cfg_insight=self.context.get('cfg_insight').llm_str(),
            # structure_insight="\n".join([insight.llm_str() for insight in self.context['structure_insight']]),
            source_insight=self.context.get('source').llm_str()
        )
        response = self._invoke(prompt)
        return response
