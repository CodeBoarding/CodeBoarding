import os

from dotenv import load_dotenv
from langchain_google_genai import ChatGoogleGenerativeAI
from langgraph.prebuilt import create_react_agent
from langchain_core.messages import HumanMessage, SystemMessage, AIMessage
from langchain_core.output_parsers import JsonOutputParser
from langchain_core.prompts import PromptTemplate
from pydantic import BaseModel, Field

from agent.tools import read_module_tool
from agent.prompts import CFG_PROMPT_TEXT, SYSTEM_MESSAGE


class Component(BaseModel):
    name: str = Field(description="Name of the component.")
    description: str = Field(description="High level description of the component.")
    communication: str = Field(description="How the component communicates with other components.")


class InterestingModules(BaseModel):
    interesting_modules: list[Component] = Field(
        description="List of the interesting python modules from the control flow graph.")


class AbstractionAgent:
    def __init__(self, project_name):
        self._setup_env_vars()
        self.project_name = project_name
        self.llm = ChatGoogleGenerativeAI(
            model="gemini-2.0-flash",
            temperature=0,
            max_retries=2,
            google_api_key=self.api_key
        )
        self.json_out_parser = JsonOutputParser(pydantic_object=InterestingModules)
        self.cfg_prompt = PromptTemplate(template=CFG_PROMPT_TEXT,
                                         input_variables=["project_name", "cfg_str"],
                                         partial_variables={
                                             "format_instructions": self.json_out_parser.get_format_instructions()})

        self.agent = create_react_agent(model=self.llm, tools=[read_module_tool])

    def get_interesting_modules(self, cfg_str):
        ask_for_abstraction = self.cfg_prompt.format(project_name=self.project_name, cfg_str=cfg_str)
        response = self.agent.invoke(
            {"messages": [SystemMessage(content=SYSTEM_MESSAGE), HumanMessage(content=ask_for_abstraction)]})
        agent_response = response["messages"][-1]
        assert isinstance(agent_response, AIMessage), f"Expected AIMessage, but got {type(agent_response)}"

        return self.json_out_parser.parse(agent_response.content)

    def _setup_env_vars(self):
        load_dotenv()
        self.api_key = os.getenv("GOOGLE_API_KEY")
