import json
import os
import re
from typing import List

from dotenv import load_dotenv
from langchain.prompts import PromptTemplate
from langchain.schema import SystemMessage, HumanMessage, AIMessage
from langchain_core.exceptions import OutputParserException
from langchain_core.output_parsers import PydanticOutputParser
from langchain_google_genai import ChatGoogleGenerativeAI
from langgraph.prebuilt import create_react_agent
from pydantic import BaseModel, Field

from agent.prompts import CFG_MESSAGE, STRUCTURE_MESSAGE, SOURCE_MESSAGE, SYSTEM_MESSAGE
from agent.tools import CodeExplorerTool

class Component(BaseModel):
    name: str = Field(description="Name of the abstract component")
    description: str = Field(description="A short description of the component.")
    qualified_name: List[str] = Field(description="A list of qualified names of related methods and classes to the component.")

    def llm_str(self):
        n = f"**Component:** `{self.name}`"
        d = f"   - *Description*: {self.description}"
        qn = ""
        if self.qualified_name:
            qn += "   - *Related Classes/Methods*: "
            qn += ", ".join(f"`{q}`" for q in self.qualified_name)
        return "\n".join([n, d, qn]).strip()

class AnalysisInsights(BaseModel):
    abstract_components: List[Component] = Field(description="List of the abstract components identified in the project.")

    def llm_str(self):
        if not self.abstract_components:
            return "No abstract components found."
        title = "# 📦 Abstract Components Overview\n"
        body = "\n\n".join(ac.llm_str() for ac in self.abstract_components)
        return title + body


def custom_parse(response):
    """Custom parsing logic for CFG response."""
    # This is a placeholder. You can implement your own logic here.
    # For example, you can use regex or other parsing techniques.
    pattern = re.compile(r"```(json)?(.*)", re.DOTALL)
    match = pattern.search(response)
    if match:
        json_str = match.group(0)
        json_str = json_str.split("```json" )[-1].strip()
        json_str = json_str.split("```")[0].strip()
        return AnalysisInsights.model_validate(json.loads(json_str))
    raise OutputParserException(response)


class AbstractionAgent:
    def __init__(self, root_dir, project_name):
        self._setup_env_vars()
        self.root_dir = root_dir
        self.project_name = project_name
        self.llm = ChatGoogleGenerativeAI(
            model="gemini-2.0-flash",
            temperature=0,
            max_retries=2,
            google_api_key=self.api_key
        )
        self.context = {"structure_insight": []}  # Store evolving insights here

        # Define your prompts for each stage, and their parsers
        self.parsers = {
            "cfg": PydanticOutputParser(pydantic_object=AnalysisInsights),
            "structure": PydanticOutputParser(pydantic_object=AnalysisInsights),
        }
        self.prompts = {
            "cfg": PromptTemplate(template=CFG_MESSAGE, input_variables=["project_name", "cfg_str"],
                                  partial_variables={
                                      "format_instructions": self.parsers["cfg"].get_format_instructions()}),
            "structure": PromptTemplate(template=STRUCTURE_MESSAGE, input_variables=["cfg_insight", "structure_graph"],
                                        partial_variables={"format_instructions": self.parsers["structure"].get_format_instructions()}),
            "source": PromptTemplate(template=SOURCE_MESSAGE,input_variables=["insight_so_far"]),
        }

        # Define or import agents/tools for deep reading, as needed
        self.read_module_tool = CodeExplorerTool(root_project_dir=self.root_dir)
        self.agent = create_react_agent(model=self.llm, tools=[self.read_module_tool])

    def _setup_env_vars(self):
        load_dotenv()
        self.api_key = os.getenv("GOOGLE_API_KEY")

    def step_cfg(self, cfg_str):
        prompt = self.prompts["cfg"].format(project_name=self.project_name, cfg_str=cfg_str)
        response = self._invoke(prompt)
        try:
            parsed = self.parsers["cfg"].parse(response)
        except OutputParserException as e:
            print(f"[Error] Failed to parse CFG response: {e}")
            parsed = custom_parse(response)
        self.context['cfg_insight'] = parsed  # Store for next step
        return parsed

    def step_structure(self, structure_graph):
        prompt = self.prompts["structure"].format(
            cfg_insight=self.context.get('cfg_insight').llm_str(),
            structure_graph=structure_graph
        )
        response = self._invoke(prompt)
        parsed = self.parsers["structure"].parse(response)
        self.context['structure_insight'].append(parsed)
        return parsed

    def step_source(self):
        insight_str = ""
        for insight_type, anaylsys_insight in self.context.items():
            insight_str += f"## {insight_type.capitalize()} Insight\n"
            if type(anaylsys_insight) is list:
                insight_str += "\n".join([f"- {insight.llm_str()}" for insight in anaylsys_insight]) + "\n\n"
            else:
                insight_str += anaylsys_insight.llm_str() + "\n\n"

        prompt = self.prompts["source"].format(
            insight_so_far=insight_str,
        )
        response = self._invoke(prompt)
        return response

    def _invoke(self, prompt):
        """Unified agent invocation method."""
        response = self.agent.invoke(
            {"messages": [SystemMessage(content=SYSTEM_MESSAGE), HumanMessage(content=prompt)]}
        )
        agent_response = response["messages"][-1]
        assert isinstance(agent_response, AIMessage), f"Expected AIMessage, but got {type(agent_response)}"
        return agent_response.content
