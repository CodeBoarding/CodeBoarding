import logging

from langchain.prompts import PromptTemplate
from langchain_core.exceptions import OutputParserException
from langchain_core.output_parsers import PydanticOutputParser

from agents.agent import CodeBoardingAgent, AnalysisInsights
from agents.prompts import CFG_MESSAGE, SOURCE_MESSAGE, SYSTEM_MESSAGE, CONCLUSIVE_ANALYSIS_MESSAGE


class AbstractionAgent(CodeBoardingAgent):
    def __init__(self, root_dir, root_repo_dir, project_name):
        super().__init__(root_dir, root_repo_dir, SYSTEM_MESSAGE)

        self.project_name = project_name

        self.context = {"structure_insight": []}  # Store evolving insights here

        # Define your prompts for each stage, and their parsers
        self.parsers = {
            "cfg": PydanticOutputParser(pydantic_object=AnalysisInsights),
            "source": PydanticOutputParser(pydantic_object=AnalysisInsights),
            "markdown": PydanticOutputParser(pydantic_object=AnalysisInsights),
        }

        self.prompts = {
            "cfg": PromptTemplate(template=CFG_MESSAGE, input_variables=["project_name", "cfg_str"],
                                  partial_variables={
                                      "format_instructions": self.parsers["cfg"].get_format_instructions()}),
            "source": PromptTemplate(template=SOURCE_MESSAGE, input_variables=["insight_so_far"],
                                     partial_variables={
                                         "format_instructions": self.parsers["source"].get_format_instructions()}),
            "markdown": PromptTemplate(template=CONCLUSIVE_ANALYSIS_MESSAGE,
                                       input_variables=["project_name", "cfg_insight", "structure_insight",
                                                        "source_insight"],
                                       partial_variables={
                                           "format_instructions": self.parsers["markdown"].get_format_instructions()}),
        }

    def step_cfg(self, cfg_str):
        logging.info(f"[INFO] Analyzing CFG for project: {self.project_name}")
        prompt = self.prompts["cfg"].format(project_name=self.project_name, cfg_str=cfg_str)
        response = self._invoke(prompt)
        parsed = self.parsers["cfg"].parse(response)
        self.context['cfg_insight'] = parsed  # Store for next step
        return parsed

    def step_packages(self, packages):
        logging.info(f"[INFO] Analyzing Packages for project: {self.project_name}")
        insight_str = ""
        for pkg in packages:
            insight_str += f"- `{pkg}`\n"
        insight_str += "\n"
        self.context['packages'] = insight_str


    def step_source(self):
        logging.info(f"[INFO] Analyzing Source for project: {self.project_name}")
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

    def generate_markdown(self, rerun=3):
        if rerun < 0:
            raise Exception("Max rerun attempts exceeded.")
        logging.info(f"[INFO] Generating markdown for project: {self.project_name}")
        prompt = self.prompts["markdown"].format(
            project_name=self.project_name,
            cfg_insight=self.context.get('cfg_insight').llm_str(),
            # structure_insight="\n".join([insight.llm_str() for insight in self.context['structure_insight']]),
            source_insight=self.context.get('source').llm_str()
        )
        response = self._invoke(prompt)
        try:
            return self.parsers["markdown"].parse(response)
        except OutputParserException as e:
            logging.info(response)
            logging.info.infoint(f"[Warn] Error in parsing markdown: {e}")
            return self.generate_markdown(rerun=rerun - 1)
        except Exception as e:
            logging.info(response)
            logging.info(f"[Warn] Error in generating markdown: {e}")
            return self.generate_markdown(rerun=rerun - 1)
