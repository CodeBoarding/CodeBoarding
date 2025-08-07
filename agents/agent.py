import logging
import os
import time

from dotenv import load_dotenv
from google.api_core.exceptions import ResourceExhausted
from langchain_core.messages import SystemMessage, HumanMessage, AIMessage
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_openai import ChatOpenAI
from langchain_anthropic import ChatAnthropic
from langchain_aws import ChatBedrockConverse
from langchain_ollama import ChatOllama
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
        self.llm = self._initialize_llm()
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
        # Check for API keys in priority order: OpenAI > Anthropic > Google > AWS Bedrock > Ollama
        self.openai_api_key = os.getenv("OPENAI_API_KEY")
        self.openai_base_url = os.getenv("OPENAI_BASE_URL")
        self.anthropic_api_key = os.getenv("ANTHROPIC_API_KEY")
        self.google_api_key = os.getenv("GOOGLE_API_KEY")
        self.aws_bearer_token = os.getenv("AWS_BEARER_TOKEN_BEDROCK")
        self.aws_region = os.getenv("AWS_DEFAULT_REGION", "us-east-1")
        self.ollama_base_url = os.getenv("OLLAMA_BASE_URL")

    def _initialize_llm(self):
        """Initialize LLM based on available API keys with priority order."""
        if self.openai_api_key:
            logging.info("Using OpenAI LLM")
            return ChatOpenAI(
                model="gpt-4o",
                temperature=0,
                max_tokens=None,
                timeout=None,
                max_retries=2,
                api_key=self.openai_api_key,
                base_url=self.openai_base_url
            )
        elif self.anthropic_api_key:
            logging.info("Using Anthropic LLM")
            return ChatAnthropic(
                model="claude-3-5-sonnet-20241022",
                temperature=0,
                max_tokens=None,
                timeout=None,
                max_retries=2,
                api_key=self.anthropic_api_key,
            )
        elif self.google_api_key:
            logging.info("Using Google Gemini LLM")
            return ChatGoogleGenerativeAI(
                model="gemini-2.5-pro",
                temperature=0,
                max_tokens=None,
                timeout=None,
                max_retries=2,
                api_key=self.google_api_key,
            )
        elif self.aws_bearer_token:
            logging.info("Using AWS Bedrock Converse LLM")
            return ChatBedrockConverse(
                model="us.anthropic.claude-3-7-sonnet-20250219-v1:0",
                temperature=0,
                max_tokens=4096,
                region_name=self.aws_region,
                credentials_profile_name=None,
            )
        elif self.ollama_base_url:
            logging.info("Using Ollama LLM")
            return ChatOllama(
                model="qwen3:32b",
                base_url=self.ollama_base_url,
                temperature=0,
                max_tokens=None,
                timeout=None,
                max_retries=2,
            )
        else:
            raise ValueError(
                "No valid API key found. Please set one of: OPENAI_API_KEY, ANTHROPIC_API_KEY, "
                "GOOGLE_API_KEY, or AWS_BEARER_TOKEN_BEDROCK"
            )

    def _invoke(self, prompt) -> str:
        """Unified agent invocation method."""
        max_retries = 5
        for attempt in range(max_retries):
            try:
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
        logging.error("Max retries reached. Failed to get response from the agent.")
        return "Could not get response from the agent."

    def _parse_invoke(self, prompt, type):
        response = self._invoke(prompt)
        return self._parse_response(prompt, response, type)

    def _parse_response(self, prompt, response, return_type, max_retries=5):
        if max_retries == 0:
            logging.error(f"Max retries reached for parsing response: {response}")
            raise Exception(f"Max retries reached for parsing response: {response}")

        extractor = create_extractor(self.llm, tools=[return_type], tool_choice=return_type.__name__)
        if response is None or response.strip() == "":
            logging.error(f"Empty response for prompt: {prompt}")
        try:
            result = extractor.invoke(response)["responses"][0]
            return return_type.model_validate(result)
        except (ResourceExhausted, Exception) as e:
            logging.error(f"Resource exhausted or parsing error, retrying... in 60 seconds: Type({type(e)}) {e}")
            time.sleep(60)
            return self._parse_response(prompt, response, return_type, max_retries - 1)

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