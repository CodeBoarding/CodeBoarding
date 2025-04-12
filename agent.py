from langchain.tools import tool
from langchain_google_genai import ChatGoogleGenerativeAI
from langgraph.prebuilt import create_react_agent
from langchain_core.messages import HumanMessage, SystemMessage, AIMessage
from langchain_core.output_parsers import JsonOutputParser
from langchain_core.prompts import PromptTemplate
from pydantic import BaseModel, Field

import importlib
import inspect

from prompts import CFG_PROMPT_TEXT, SYSTEM_MESSAGE


class Component(BaseModel):
    name: str = Field(description="Name of the component.")
    description: str = Field(description="High level description of the component.")
    communication: str = Field(description="How the component communicates with other components.")

class InterestingModules(BaseModel):
    interesting_modules: list[Component] = Field(
        description="List of the interesting python modules from the control flow graph.")

class ModuleInput(BaseModel):
    python_code_reference: str = Field(
        description="Python code reference which to be loaded as source code. Example langchain.tools.tool")


@tool("read_source_code", args_schema=ModuleInput)
def read_module_tool(python_code_reference: str) -> str:
    """
    Tool which can read the source code of a python code reference. You have to provide the complete path to the module.
    Like langchain.tools.tool or langchain_core.output_parsers.JsonOutputParser and the return result will be the source code.
    """
    try:
        parts = python_code_reference.split('.')
        path, module, attrs = None, None, None
        for i in range(len(parts), 0, -1):
            try:
                path = '.'.join(parts[:i])
                module = importlib.import_module(path)
                attrs = parts[i:]
                break
            except ModuleNotFoundError:
                continue
        if module is None or attrs is None:
            raise ImportError(f"Module {path} not found.")

        if len(attrs) == 2:  # high chance that this is a method in a class!
            obj = getattr(module, attrs[0])
            if hasattr(obj, attrs[1]):
                obj = getattr(obj, attrs[1])
                return f"Source code for {python_code_reference}:\n{inspect.getsource(obj)}"

        # last resolution try to import and give any source code!
        for i in range(len(attrs), 0, -1):
            try:
                attribute = '.'.join(attrs[:i])
                obj = getattr(module, attribute)
                return f"Source code for {path + '.' + attribute}:\n{inspect.getsource(obj)}"
            except Exception as e:
                print("Bad import ", e)
                continue
        raise ImportError(f"Attribute {'.'.join(attrs)} not found in module {path}.")
    except ImportError as e:
        return f"Error: {e}. Please provide a valid python code reference."


class AbstractionAgent:
    def __init__(self, project_name):
        self.project_name = project_name
        self.llm = ChatGoogleGenerativeAI(
            model="gemini-2.0-flash",
            temperature=0,
            max_retries=2,
        )
        self.json_out_parser = JsonOutputParser(pydantic_object=InterestingModules)
        self.cfg_prompt = PromptTemplate(template=CFG_PROMPT_TEXT,
                                         input_variables=["project_name", "cfg_str"],
                                         partial_variables={
                                             "format_instructions": self.json_out_parser.get_format_instructions()})
        # self.interesting_modules_chain = cfg_prompt | self.llm | interesting_modules_parser

        self.agent = create_react_agent(model=self.llm, tools=[read_module_tool])

    def get_interesting_modules(self, cfg_str, nodes):
        ask_for_abstraction = self.cfg_prompt.format(project_name=self.project_name, cfg_str=cfg_str)
        response = self.agent.invoke(
            {"messages": [SystemMessage(content=SYSTEM_MESSAGE), HumanMessage(content=ask_for_abstraction)]})
        agent_response = response["messages"][-1]
        assert isinstance(agent_response, AIMessage), f"Expected AIMessage, but got {type(agent_response)}"

        return self.json_out_parser.parse(agent_response.content)
