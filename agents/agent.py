import os
from typing import List

from dotenv import load_dotenv
from langchain_core.messages import SystemMessage, HumanMessage, AIMessage
from langchain_google_genai import ChatGoogleGenerativeAI
from langgraph.prebuilt import create_react_agent
from pydantic import BaseModel, Field

from agents.tools import CodeExplorerTool
from agents.tools.read_packages import PackageRelationsTool
from agents.tools.read_structure import CodeStructureTool


class Component(BaseModel):
    name: str = Field(description="Name of the abstract component")
    description: str = Field(description="A short description of the component.")
    qualified_names: List[str] = Field(
        description="A list of qualified names of related methods and classes to the component.")

    def llm_str(self):
        n = f"**Component:** `{self.name}`"
        d = f"   - *Description*: {self.description}"
        qn = ""
        if self.qualified_names:
            qn += "   - *Related Classes/Methods*: "
            qn += ", ".join(f"`{q}`" for q in self.qualified_names)
        return "\n".join([n, d, qn]).strip()


class SubControlFlowGraph(BaseModel):
    sub_graph: str = Field(description="The sub control flow graph in DOT format.")


class AnalysisInsights(BaseModel):
    abstract_components: List[Component] = Field(
        description="List of the abstract components identified in the project.")

    def llm_str(self):
        if not self.abstract_components:
            return "No abstract components found."
        title = "# 📦 Abstract Components Overview\n"
        body = "\n\n".join(ac.llm_str() for ac in self.abstract_components)
        return title + body


class MarkdownOutput(BaseModel):
    content: str = Field(description="Analysis with abstract diagram in markdown format.")
    components: List[Component] = Field(description="List of the abstract components from the diagram.")

    def llm_str(self):
        return self.content.strip()


class CodeBoardingAgent:
    def __init__(self, root_dir, system_message):
        self._setup_env_vars()
        self.llm = ChatGoogleGenerativeAI(
            model="gemini-2.0-flash",
            temperature=0,
            max_retries=2,
            google_api_key=self.api_key
        )
        self.read_source_code = CodeExplorerTool(root_project_dir=root_dir)
        self.read_packages_tool = PackageRelationsTool(root_project_dir=root_dir)
        self.read_structure_tool = CodeStructureTool(root_project_dir=root_dir)
        self.agent = create_react_agent(model=self.llm, tools=[self.read_source_code, self.read_packages_tool,
                                                               self.read_structure_tool])
        self.system_message = SystemMessage(content=system_message)

    def _setup_env_vars(self):
        load_dotenv()
        self.api_key = os.getenv("GOOGLE_API_KEY")

    def _invoke(self, prompt):
        """Unified agent invocation method."""
        response = self.agent.invoke(
            {"messages": [self.system_message, HumanMessage(content=prompt)]}
        )
        agent_response = response["messages"][-1]
        assert isinstance(agent_response, AIMessage), f"Expected AIMessage, but got {type(agent_response)}"
        return agent_response.content
