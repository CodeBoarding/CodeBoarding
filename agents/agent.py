import logging
import os
import time

from dotenv import load_dotenv
from google.api_core.exceptions import ResourceExhausted
from langchain_aws import ChatBedrockConverse
from langchain_core.messages import SystemMessage, HumanMessage, AIMessage
from langgraph.prebuilt import create_react_agent
from trustcall import create_extractor

from agents.agent_responses import AnalysisInsights
from agents.tools import CodeReferenceReader, CodeStructureTool, PackageRelationsTool, FileStructureTool, GetCFGTool, \
    MethodInvocationsTool, ReadFileTool
from agents.tools.external_deps import ExternalDepsTool
from agents.tools.read_docs import ReadDocsTool
from static_analyzer.reference_lines import find_fqn_location


class CodeBoardingAgent:
    def __init__(self, repo_dir, output_dir, cfg, system_message):
        self._setup_env_vars()
        self.llm = ChatBedrockConverse(
            model_id="us.anthropic.claude-3-7-sonnet-20250219-v1:0",  # Cross-region inference profile format
            region_name="us-east-1",
            temperature=0
        )
        self.read_source_reference = CodeReferenceReader(repo_dir=repo_dir)
        self.read_packages_tool = PackageRelationsTool(analysis_dir=output_dir)
        self.read_structure_tool = CodeStructureTool(analysis_dir=output_dir)
        self.read_file_structure = FileStructureTool(repo_dir=repo_dir)
        self.read_cfg_tool = GetCFGTool(cfg=cfg)
        self.read_method_invocations_tool = MethodInvocationsTool(cfg=cfg)
        self.read_file_tool = ReadFileTool(repo_dir=repo_dir)
        self.read_docs = ReadDocsTool(repo_dir=repo_dir)
        self.external_deps_tool = ExternalDepsTool(repo_dir=repo_dir)

        self.agent = create_react_agent(model=self.llm, tools=[self.read_source_reference, self.read_packages_tool,
                                                               self.read_file_structure, self.read_structure_tool,
                                                               self.read_file_tool])
        self.system_message = SystemMessage(content=system_message)

    def _setup_env_vars(self):
        load_dotenv()
        # When compiling for VSCode paste the key here directly!
        # As we cannot pass env files to someone's system
        self.api_key = os.getenv("GOOGLE_API_KEY")

    def _invoke(self, prompt):
        """Unified agent invocation method."""
        max_retries = 3
        for attempt in range(max_retries):
            try:
                print(prompt)
                response = self.agent.invoke(
                    {"messages": [self.system_message, HumanMessage(content=prompt)]}
                )
                agent_response = response["messages"][-1]
                assert isinstance(agent_response, AIMessage), f"Expected AIMessage, but got {type(agent_response)}"
                if type(agent_response.content) == str:
                    return agent_response.content
                if type(agent_response.content) == list:
                    return "".join([message for message in agent_response.content])
            except (ResourceExhausted, Exception) as e:
                logging.error(f"Resource exhausted, retrying... in 60 seconds: Type({type(e)}) {e}")
                time.sleep(60)  # Wait before retrying

    def _parse_invoke(self, prompt, type):
        response = self._invoke(prompt)
        return self._parse_response(prompt, response, type)

    def _parse_response(self, prompt, response, return_type):
        extractor = create_extractor(self.llm, tools=[return_type], tool_choice=return_type.__name__)
        if response.strip() == "":
            logging.error(f"Empty response for prompt: {prompt}")
        result = extractor.invoke(response)["responses"][0]
        return return_type.model_validate(result)

    def fix_source_code_reference_lines(self, analysis: AnalysisInsights):
        for component in analysis.components:
            for reference in component.referenced_source_code:
                file_ref, file_string = self.read_source_reference.read_file(reference.qualified_name)
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
                    logging.warning(f"Error finding reference lines for {reference.qualified_name}: {e}")

        return analysis
