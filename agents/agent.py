import logging
import os

from dotenv import load_dotenv
from langchain_core.exceptions import OutputParserException
from langchain_core.messages import SystemMessage, HumanMessage, AIMessage
from langchain_google_genai import ChatGoogleGenerativeAI
from langgraph.prebuilt import create_react_agent

from agents.agent_responses import AnalysisInsights
from agents.tools import CodeExplorerTool
from agents.tools.read_packages import PackageRelationsTool
from agents.tools.read_structure import CodeStructureTool
from static_analyzer.reference_lines import find_fqn_location


class CodeBoardingAgent:
    def __init__(self, repo_dir, output_dir, system_message):
        self._setup_env_vars()
        self.llm = ChatGoogleGenerativeAI(
            model="gemini-2.5-flash-preview-05-20",
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
        load_dotenv()
        # When compiling for VSCode paste the key here directly!
        # As we cannot pass env files to someone's system
        self.api_key = os.getenv("GOOGLE_API_KEY")

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

    def fix_source_code_reference_lines(self, analysis: AnalysisInsights):
        for component in analysis.components:
            for reference in component.referenced_source_code:
                file_ref, file_string = self.read_source_code.read_file(reference.qualified_name)
                if file_ref is None:
                    continue
                reference.reference_file = str(file_ref)
                file_string = "\n".join(file_string.split("\n")[1:])
                try:
                    qname = reference.qualified_name.replace(":", ".")
                    parts = qname.split(".")
                    for i in range(len(parts)):
                        sub_fqn = ".".join(parts[i:])
                        result = find_fqn_location(file_string, sub_fqn)
                        if result:
                            reference.reference_start_line = result[0]
                            reference.reference_end_line = result[1]
                            break
                except Exception as e:
                    logging.error(f"Error finding reference lines for {reference.qualified_name}: {e}")

        return analysis
