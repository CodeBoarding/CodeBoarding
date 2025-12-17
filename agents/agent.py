import json
import logging
import os
import time
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from google.api_core.exceptions import ResourceExhausted
from langchain_anthropic import ChatAnthropic
from langchain_aws import ChatBedrockConverse
from langchain_core.exceptions import OutputParserException
from langchain_core.language_models import BaseChatModel
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

from monitoring.callbacks import MonitoringCallback
from monitoring.mixin import MonitoringMixin
from agents.tools import (
    CodeReferenceReader,
    CodeStructureTool,
    PackageRelationsTool,
    FileStructureTool,
    GetCFGTool,
    MethodInvocationsTool,
    ReadFileTool,
    ReadDocsTool,
)
from agents.tools.external_deps import ExternalDepsTool
from static_analyzer.analysis_result import StaticAnalysisResults
from static_analyzer.reference_resolve_mixin import ReferenceResolverMixin

logger = logging.getLogger(__name__)

MONITORING_CALLBACK = MonitoringCallback()


class CodeBoardingAgent(ReferenceResolverMixin, MonitoringMixin):
    def __init__(self, repo_dir: Path, static_analysis: StaticAnalysisResults, system_message: str):
        ReferenceResolverMixin.__init__(self, repo_dir, static_analysis)
        MonitoringMixin.__init__(self)
        self._setup_env_vars()
        self.llm = self._initialize_llm()
        self.extractor_llm = self._initialize_llm()
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
        self.vercel_api_key = os.getenv("VERCEL_API_KEY")
        self.vercel_base_url = os.getenv("VERCEL_BASE_URL", "https://api.vercel.ai/v1")
        self.anthropic_api_key = os.getenv("ANTHROPIC_API_KEY")
        self.google_api_key = os.getenv("GOOGLE_API_KEY")
        self.aws_bearer_token = os.getenv("AWS_BEARER_TOKEN_BEDROCK")
        self.aws_region = os.getenv("AWS_DEFAULT_REGION", "us-east-1")
        self.cerebras_api_key = os.getenv("CEREBRAS_API_KEY")
        self.ollama_base_url = os.getenv("OLLAMA_BASE_URL")

        # Model selection via environment variable
        self.codeboarding_model = os.getenv("CODEBOARDING_MODEL")

    def _initialize_llm(self):
        """Initialize LLM based on available API keys with priority order."""
        model_name: str | None = None
        model: BaseChatModel

        if self.openai_api_key:
            model_name = self.codeboarding_model if self.codeboarding_model else "gpt-4o"
            logger.info(f"Using OpenAI LLM with model: {model_name}")
            model = ChatOpenAI(
                model=model_name,
                temperature=0,
                max_tokens=None,  # type: ignore[call-arg]
                timeout=None,
                max_retries=0,
                api_key=self.openai_api_key,  # type: ignore[arg-type]
                base_url=self.openai_base_url,
            )
        elif self.vercel_api_key:
            model_name = self.codeboarding_model if self.codeboarding_model else "gemini-2.5-flash"
            logger.info(f"Using Vercel AI Gateway with model: {model_name}")
            model = ChatOpenAI(
                model=model_name,
                temperature=0,
                max_tokens=None,  # type: ignore[call-arg]
                timeout=None,
                max_retries=0,
                api_key=self.vercel_api_key,  # type: ignore[arg-type]
                base_url=self.vercel_base_url,
            )
        elif self.anthropic_api_key:
            model_name = self.codeboarding_model if self.codeboarding_model else "claude-3-7-sonnet-20250219"
            logger.info(f"Using Anthropic LLM with model: {model_name}")
            model = ChatAnthropic(
                model=model_name,  # type: ignore[call-arg]
                temperature=0,
                max_tokens=8192,  # type: ignore[call-arg]
                timeout=None,
                max_retries=0,
                api_key=self.anthropic_api_key,  # type: ignore[arg-type]
            )
        elif self.google_api_key:
            model_name = self.codeboarding_model if self.codeboarding_model else "gemini-3-flash-preview"
            logger.info(f"Using Google Gemini LLM with model: {model_name}")
            model = ChatGoogleGenerativeAI(
                model=model_name,
                temperature=0,
                max_tokens=None,
                timeout=None,
                max_retries=0,
                api_key=self.google_api_key,
            )
        elif self.aws_bearer_token:
            model_name = (
                self.codeboarding_model if self.codeboarding_model else "us.anthropic.claude-3-7-sonnet-20250219-v1:0"
            )
            logger.info(f"Using AWS Bedrock Converse LLM with model: {model_name}")
            model = ChatBedrockConverse(
                model=model_name,
                temperature=0,
                max_tokens=4096,
                region_name=self.aws_region,
                credentials_profile_name=None,
            )
        elif self.cerebras_api_key:
            model_name = self.codeboarding_model if self.codeboarding_model else "gpt-oss-120b"
            logger.info(f"Using Cerebras LLM with model: {model_name}")
            model = ChatCerebras(
                model=model_name,
                temperature=0,
                max_tokens=None,
                timeout=None,
                max_retries=0,
                api_key=self.cerebras_api_key,  # type: ignore[arg-type]
            )
        elif self.ollama_base_url:
            model_name = self.codeboarding_model if self.codeboarding_model else "qwen3:30b"
            logging.info(f"Using Ollama LLM with model: {model_name}")
            model = ChatOllama(model=model_name, base_url=self.ollama_base_url, temperature=0.6)
        else:
            raise ValueError(
                "No valid API key found. Please set one of: OPENAI_API_KEY, ANTHROPIC_API_KEY, "
                "GOOGLE_API_KEY, or AWS_BEARER_TOKEN_BEDROCK"
            )
        self.agent_monitoring_callback.model_name = model_name
        MONITORING_CALLBACK.model_name = model_name
        return model

    def _invoke(self, prompt, callbacks: list | None = None) -> str:
        """Unified agent invocation method."""
        max_retries = 5
        for _ in range(max_retries):
            try:
                callback_list = callbacks or []
                # Always append monitoring callback - logging config controls output
                callback_list.append(MONITORING_CALLBACK)
                callback_list.append(self.agent_monitoring_callback)

                response = self.agent.invoke(
                    {"messages": [self.system_message, HumanMessage(content=prompt)]},
                    config={"callbacks": callback_list, "recursion_limit": 40},
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
            config = {"callbacks": [MONITORING_CALLBACK, self.agent_monitoring_callback]}
            result = extractor.invoke(return_type.extractor_str() + response, config=config)  # type: ignore[arg-type]
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
            config = {"callbacks": [MONITORING_CALLBACK, self.agent_monitoring_callback]}
            return chain.invoke({"adjective": message_content}, config=config)
        except (ValidationError, OutputParserException):
            for k, v in json.loads(message_content).items():
                try:
                    return self._try_parse(json.dumps(v), parser)
                except:
                    pass
        raise ValueError(f"Couldn't parse {message_content}")
