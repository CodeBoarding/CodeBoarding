import json
import logging
import os
import time
from pathlib import Path

from dotenv import load_dotenv
from google.api_core.exceptions import ResourceExhausted
from langchain_anthropic import ChatAnthropic
from langchain_aws import ChatBedrockConverse
from langchain_core.exceptions import OutputParserException
from langchain_core.messages import SystemMessage, HumanMessage, AIMessage
from langchain_core.output_parsers import PydanticOutputParser
from langchain_core.prompts import PromptTemplate
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_ollama import ChatOllama
from langchain_cerebras import ChatCerebras
from langchain_openai import ChatOpenAI
from langgraph.prebuilt import create_react_agent
from pydantic import ValidationError
from trustcall import create_extractor

from agents.tools import (
    CodeReferenceReader,
    CodeStructureTool,
    PackageRelationsTool,
    FileStructureTool,
    GetCFGTool,
    MethodInvocationsTool,
    ReadFileTool,
)
from agents.tools.external_deps import ExternalDepsTool
from agents.tools.read_docs import ReadDocsTool
from static_analyzer.analysis_result import StaticAnalysisResults
from static_analyzer.reference_resolve_mixin import ReferenceResolverMixin

logger = logging.getLogger(__name__)


class CodeBoardingAgent(ReferenceResolverMixin):
    def __init__(self, repo_dir: Path, static_analysis: StaticAnalysisResults, system_message: str):
        super().__init__(repo_dir, static_analysis)
        self._setup_env_vars()
        self.llm = self._initialize_llm()
        self.extractor_llm = self._initialize_llm()
        self._monitoring_callback = None  # Used by monitor decorator
        self.repo_dir = repo_dir
        self.read_source_reference = CodeReferenceReader(static_analysis=static_analysis)
        self.read_packages_tool = PackageRelationsTool(static_analysis=static_analysis)
        self.read_structure_tool = CodeStructureTool(static_analysis=static_analysis)
        self.read_file_structure = FileStructureTool(repo_dir=repo_dir)
        self.read_cfg_tool = GetCFGTool(static_analysis=static_analysis)
        self.read_method_invocations_tool = MethodInvocationsTool(static_analysis=static_analysis)
        self.read_file_tool = ReadFileTool(repo_dir=repo_dir)
        self.read_docs = ReadDocsTool(repo_dir=repo_dir)
        self.external_deps_tool = ExternalDepsTool(repo_dir=repo_dir)

        self.agent = create_react_agent(
            model=self.llm,
            tools=[
                self.read_source_reference,
                self.read_file_tool,
                self.read_file_structure,
                self.read_structure_tool,
                self.read_packages_tool,
            ],
        )
        self.static_analysis = static_analysis
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
        self.cerebras_api_key = os.getenv("CEREBRAS_API_KEY")
        self.ollama_base_url = os.getenv("OLLAMA_BASE_URL")

    def _initialize_llm(self):
        """Initialize LLM based on available API keys with priority order."""
        if self.openai_api_key:
            logger.info("Using OpenAI LLM")
            return ChatOpenAI(
                model="gpt-4o",
                temperature=0,
                max_tokens=None,  # type: ignore[call-arg]
                timeout=None,
                max_retries=0,
                api_key=self.openai_api_key,  # type: ignore[arg-type]
                base_url=self.openai_base_url,
            )
        elif self.anthropic_api_key:
            logger.info("Using Anthropic LLM")
            return ChatAnthropic(
                model="claude-3-7-sonnet-20250219",  # type: ignore[call-arg]
                temperature=0,
                max_tokens=8192,  # type: ignore[call-arg]
                timeout=None,
                max_retries=0,
                api_key=self.anthropic_api_key,  # type: ignore[arg-type]
            )
        elif self.google_api_key:
            logger.info("Using Google Gemini LLM")
            return ChatGoogleGenerativeAI(
                model="gemini-2.5-flash",
                temperature=0,
                max_tokens=None,
                timeout=None,
                max_retries=0,
                api_key=self.google_api_key,
            )
        elif self.aws_bearer_token:
            logger.info("Using AWS Bedrock Converse LLM")
            return ChatBedrockConverse(
                model="us.anthropic.claude-3-7-sonnet-20250219-v1:0",
                temperature=0,
                max_tokens=4096,
                region_name=self.aws_region,
                credentials_profile_name=None,
            )
        elif self.cerebras_api_key:
            logger.info("Using Cerebras LLM")
            return ChatCerebras(
                model="gpt-oss-120b",
                temperature=0,
                max_tokens=None,
                timeout=None,
                max_retries=0,
                api_key=self.cerebras_api_key,  # type: ignore[arg-type]
            )
        elif self.ollama_base_url:
            logging.info("Using Ollama LLM")
            return ChatOllama(model="qwen3:30b", base_url=self.ollama_base_url, temperature=0.6)
        else:
            raise ValueError(
                "No valid API key found. Please set one of: OPENAI_API_KEY, ANTHROPIC_API_KEY, "
                "GOOGLE_API_KEY, or AWS_BEARER_TOKEN_BEDROCK"
            )

    def _invoke(self, prompt, callbacks: list | None = None) -> str:
        """Unified agent invocation method."""
        max_retries = 5
        for _ in range(max_retries):
            try:
                callback_list = callbacks or []
                if self._monitoring_callback:
                    callback_list.append(self._monitoring_callback)

                if callback_list:
                    response = self.agent.invoke(
                        {"messages": [self.system_message, HumanMessage(content=prompt)]},
                        config={"callbacks": callback_list, "recursion_limit": 40},
                    )
                else:
                    response = self.agent.invoke(
                        {"messages": [self.system_message, HumanMessage(content=prompt)]},
                        config={"recursion_limit": 200},
                    )
                agent_response = response["messages"][-1]
                assert isinstance(agent_response, AIMessage), f"Expected AIMessage, but got {type(agent_response)}"
                if isinstance(agent_response.content, str):
                    return agent_response.content
                if isinstance(agent_response.content, list):
                    return "".join(
                        [
                            str(message) if not isinstance(message, str) else message
                            for message in agent_response.content
                        ]
                    )

            except (ResourceExhausted, Exception) as e:
                logger.error(f"Resource exhausted, retrying... in 60 seconds: Type({type(e)}) {e}")
                time.sleep(60)  # Wait before retrying

        logger.error("Max retries reached. Failed to get response from the agent.")
        return "Could not get response from the agent."

    def _parse_invoke(self, prompt, type):
        response = self._invoke(prompt)
        assert isinstance(response, str), f"Expected a string as response type got {response}"
        return self._parse_response(prompt, response, type)

    def _parse_response(self, prompt, response, return_type, max_retries=5):
        if max_retries == 0:
            logger.error(f"Max retries reached for parsing response: {response}")
            raise Exception(f"Max retries reached for parsing response: {response}")

        extractor = create_extractor(self.llm, tools=[return_type], tool_choice=return_type.__name__)
        if response is None or response.strip() == "":
            logger.error(f"Empty response for prompt: {prompt}")
        try:
            config = {"callbacks": [self._monitoring_callback]} if self._monitoring_callback else None
            result = extractor.invoke(return_type.extractor_str() + response, config=config)
            if "responses" in result and len(result["responses"]) != 0:
                return return_type.model_validate(result["responses"][0])
            if "messages" in result and len(result["messages"]) != 0:
                message = result["messages"][0].content
                parser = PydanticOutputParser(pydantic_object=return_type)
                return self._try_parse(message, parser)
            parser = PydanticOutputParser(pydantic_object=return_type)
            return self._try_parse(response, parser)
        except IndexError as e:
            # try to parse with the json parser if possible
            logger.error(f"IndexError while parsing response: {response}, Error: {e}")
            return self._parse_response(prompt, response, return_type, max_retries - 1)
        except ResourceExhausted as e:
            logger.error(f"Resource exhausted or parsing error, retrying... in 60 seconds: Type({type(e)}) {e}")
            time.sleep(60)
            return self._parse_response(prompt, response, return_type, max_retries - 1)

    def _try_parse(self, message_content, parser):
        try:
            prompt_template = """You are an JSON expert. Here you need to extract information in the following json format: {format_instructions}

            Here is the content to parse and fix: {adjective}

            Please provide only the JSON output without any additional text."""
            prompt = PromptTemplate(
                template=prompt_template,
                input_variables=["adjective"],
                partial_variables={"format_instructions": parser.get_format_instructions()},
            )
            chain = prompt | self.extractor_llm | parser
            config = {"callbacks": [self._monitoring_callback]} if self._monitoring_callback else None
            return chain.invoke({"adjective": message_content}, config=config)
        except (ValidationError, OutputParserException):
            for k, v in json.loads(message_content).items():
                try:
                    return self._try_parse(json.dumps(v), parser)
                except:
                    pass
        raise ValueError(f"Couldn't parse {message_content}")

    def get_monitoring_results(self) -> dict:
        """Return monitoring statistics if monitoring is enabled."""
        if not self._monitoring_callback:
            return {}

        return {
            "token_usage": {
                "prompt_tokens": self._monitoring_callback.prompt_tokens,
                "completion_tokens": self._monitoring_callback.completion_tokens,
                "total_tokens": self._monitoring_callback.total_tokens,
            },
            "tool_usage": {
                "counts": dict(self._monitoring_callback.tool_counts),
                "errors": dict(self._monitoring_callback.tool_errors),
                "avg_latency_ms": {
                    tool: sum(latencies) / len(latencies) if latencies else 0
                    for tool, latencies in self._monitoring_callback.tool_latency_ms.items()
                },
            },
        }
