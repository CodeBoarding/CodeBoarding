import os
from typing import List

from dotenv import load_dotenv
from langchain_core.exceptions import OutputParserException
from langchain_core.messages import SystemMessage, HumanMessage, AIMessage
from langchain_google_genai import ChatGoogleGenerativeAI
from langgraph.prebuilt import create_react_agent
from pydantic import BaseModel, Field

from agents.tools import CodeExplorerTool
from agents.tools.read_packages import PackageRelationsTool
from agents.tools.read_structure import CodeStructureTool


class Relation(BaseModel):
    relation: str = Field(description="Single phrase used for the relationship of two components.")
    src_name: str = Field(description="Source component name")
    dst_name: str = Field(description="Target component name")

    def llm_str(self):
        return f"({self.src_name}, {self.relation}, {self.dst_name})"


class Component(BaseModel):
    name: str = Field(description="Name of the component")
    description: str = Field(description="A short description of the component.")
    related_source: List[str] = Field(
        description="A list of source code names of related methods and classes to the component."
    )
    related_source_files: List[str] = Field(
        description="A list of the source code files where the component is defined.")

    def llm_str(self):
        n = f"**Component:** `{self.name}`"
        d = f"   - *Description*: {self.description}"
        qn = ""
        if self.related_source:
            qn += "   - *Related Classes/Methods*: "
            qn += ", ".join(f"`{q}`" for q in self.related_source)
        return "\n".join([n, d, qn]).strip()


class SubControlFlowGraph(BaseModel):
    sub_graph: str = Field(description="The sub control flow graph in DOT format.")


class AnalysisInsights(BaseModel):
    description: str = Field(
        "One paragraph explaining the functionality which is represented by this graph. What the main flow is and what is its purpose.")
    components: List[Component] = Field(
        description="List of the components identified in the project.")
    components_relations: List[Relation] = Field(
        description="List of relations among the components."
    )

    def llm_str(self):
        if not self.components:
            return "No abstract components found."
        title = "# 📦 Abstract Components Overview\n"
        body = "\n".join(ac.llm_str() for ac in self.components)
        relations = "\n".join(cr.llm_str() for cr in self.components_relations)
        return title + body + relations


class CodeBoardingAgent:
    def __init__(self, repo_dir, output_dir, system_message):
        self._setup_env_vars()
        self.llm = ChatGoogleGenerativeAI(
            model="gemini-2.0-flash",
            temperature=0,
            max_retries=2,
            google_api_key=self.api_key
        )
        self.read_source_code = CodeExplorerTool(repo_dir=repo_dir)
        self.read_packages_tool = PackageRelationsTool(analysis_dir=output_dir)
        self.read_structure_tool = CodeStructureTool(analysis_dir=output_dir)
        self.agent = create_react_agent(model=self.llm, tools=[self.read_source_code, self.read_packages_tool,
                                                               self.read_structure_tool])
        self.system_message = SystemMessage(content=system_message)

    def _setup_env_vars(self):
        self.api_key = "AIzaSyCp6jlH3m0GunL3NrFHb0l7PxsioPKD4aY"

    def _invoke(self, prompt):
        """Unified agent invocation method."""
        response = self.agent.invoke(
            {"messages": [self.system_message, HumanMessage(content=prompt)]}
        )
        agent_response = response["messages"][-1]
        assert isinstance(agent_response, AIMessage), f"Expected AIMessage, but got {type(agent_response)}"
        if type(agent_response.content) == str:
            return agent_response.content
        if type(agent_response.content) == list:
            return "".join([message for message in agent_response.content])

    def _parse_invoke(self, prompt, parser, retry=3):
        if retry < 0:
            raise OutputParserException(f"Max retries reached for parsing: {prompt}")
        try:
            response = self._invoke(prompt)
            return parser.parse(response)
        except OutputParserException as e:
            return self._parse_invoke(prompt, parser, retry=retry - 1)
