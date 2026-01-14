import logging
import os
import time
from pathlib import Path
from typing import Type

import instructor
from google.api_core.exceptions import ResourceExhausted
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import SystemMessage, HumanMessage, AIMessage
from langgraph.prebuilt import create_react_agent
from pydantic import BaseModel, ValidationError

from agents.llm_config import LLM_PROVIDERS
from agents.tools.base import RepoContext
from agents.tools.toolkit import CodeBoardingToolkit
from monitoring.callbacks import MonitoringCallback
from monitoring.mixin import MonitoringMixin
from repo_utils.ignore import RepoIgnoreManager
from static_analyzer.analysis_result import StaticAnalysisResults
from static_analyzer.reference_resolve_mixin import ReferenceResolverMixin

logger = logging.getLogger(__name__)

# Initialize global monitoring callback with its own stats container to avoid ContextVar dependency
from monitoring.stats import RunStats

MONITORING_CALLBACK = MonitoringCallback(stats_container=RunStats())

INSTRUCTOR_PROVIDER_MAP = {
    "openai": "openai",
    "vercel": "openai",  # Uses OpenAI-compatible API
    "anthropic": "anthropic",
    "google": "google",
    "aws": "bedrock",
    "cerebras": "cerebras",
    "ollama": "ollama",
}


class CodeBoardingAgent(ReferenceResolverMixin, MonitoringMixin):
    _parsing_llm: BaseChatModel | None = None
    _instructor_client = None
    _instructor_provider_name: str | None = None
    _instructor_model_name: str | None = None

    def __init__(self, repo_dir: Path, static_analysis: StaticAnalysisResults, system_message: str, llm: BaseChatModel):
        ReferenceResolverMixin.__init__(self, repo_dir, static_analysis)
        MonitoringMixin.__init__(self)
        self.llm = llm
        self.repo_dir = repo_dir
        self.ignore_manager = RepoIgnoreManager(repo_dir)

        # Initialize the professional toolkit
        context = RepoContext(repo_dir=repo_dir, ignore_manager=self.ignore_manager, static_analysis=static_analysis)
        self.toolkit = CodeBoardingToolkit(context=context)

        self.agent = create_react_agent(
            model=self.llm,
            tools=self.toolkit.get_agent_tools(),
        )
        self.static_analysis = static_analysis
        self.system_message = SystemMessage(content=system_message)

    @property
    def read_source_reference(self):
        return self.toolkit.read_source_reference

    @property
    def read_packages_tool(self):
        return self.toolkit.read_packages

    @property
    def read_structure_tool(self):
        return self.toolkit.read_structure

    @property
    def read_file_structure(self):
        return self.toolkit.read_file_structure

    @property
    def read_cfg_tool(self):
        return self.toolkit.read_cfg

    @property
    def read_method_invocations_tool(self):
        return self.toolkit.read_method_invocations

    @property
    def read_file_tool(self):
        return self.toolkit.read_file

    @property
    def read_docs(self):
        return self.toolkit.read_docs

    @property
    def external_deps_tool(self):
        return self.toolkit.external_deps

    @classmethod
    def get_parsing_llm(cls) -> BaseChatModel:
        """Shared access to the small model for parsing tasks."""
        if cls._parsing_llm is None:
            parsing_model = os.getenv("PARSING_MODEL", None)
            cls._parsing_llm, _ = cls._static_initialize_llm(model_override=parsing_model, is_parsing=True)
        return cls._parsing_llm

    @classmethod
    def _get_instructor_client(cls):
        """Get or create an instructor client for structured extraction."""
        if cls._instructor_client is not None:
            return cls._instructor_client

        parsing_model = os.getenv("PARSING_MODEL", None)

        for name, config in LLM_PROVIDERS.items():
            if not config.is_active():
                continue

            model_name = parsing_model if parsing_model else config.parsing_model
            instructor_provider = INSTRUCTOR_PROVIDER_MAP.get(name)

            if instructor_provider is None:
                logger.warning(f"No instructor mapping for provider {name}, falling back to openai")
                instructor_provider = "openai"

            logger.info(f"Initializing instructor client with provider: {instructor_provider}, model: {model_name}")

            if name == "vercel":
                from openai import OpenAI

                base_url = config.get_resolved_extra_args().get("base_url")
                openai_client = OpenAI(api_key=config.get_api_key(), base_url=base_url)
                cls._instructor_client = instructor.from_openai(openai_client, mode=instructor.Mode.MD_JSON)
            elif name == "google":
                cls._instructor_client = instructor.from_provider(
                    f"google/{model_name}",
                    api_key=config.get_api_key(),
                    mode=instructor.Mode.MD_JSON,
                )
            else:
                provider_string = f"{instructor_provider}/{model_name}"
                kwargs = {}
                if name not in ["aws", "ollama"]:
                    api_key = config.get_api_key()
                    if api_key:
                        kwargs["api_key"] = api_key
                cls._instructor_client = instructor.from_provider(provider_string, **kwargs)

            cls._instructor_provider_name = name
            cls._instructor_model_name = model_name
            return cls._instructor_client

        raise ValueError("No valid LLM configuration found for instructor client")

    @staticmethod
    def _static_initialize_llm(
        model_override: str | None = None, is_parsing: bool = False
    ) -> tuple[BaseChatModel, str]:
        """Initialize LLM based on available API keys with priority order."""
        for name, config in LLM_PROVIDERS.items():
            if not config.is_active():
                continue

            # Determine model name
            default_model = config.parsing_model if is_parsing else config.agent_model
            model_name = model_override if model_override else default_model

            logger.info(f"Using {name.title()} {'Extractor ' if is_parsing else ''}LLM with model: {model_name}")

            kwargs = {
                "model": model_name,
                "temperature": config.parsing_temperature if is_parsing else config.agent_temperature,
            }
            kwargs.update(config.get_resolved_extra_args())

            if name not in ["aws", "ollama"]:
                api_key = config.get_api_key()
                kwargs["api_key"] = api_key or "no-key-required"

            model = config.chat_class(**kwargs)  # type: ignore[call-arg, arg-type]

            # Update global monitoring callback
            MONITORING_CALLBACK.model_name = model_name
            return model, model_name

        # Dynamically build error message with all possible env vars
        required_vars = []
        for config in LLM_PROVIDERS.values():
            required_vars.append(config.api_key_env)
            required_vars.extend(config.alt_env_vars)

        raise ValueError(
            f"No valid LLM configuration found. Please set one of: {', '.join(sorted(set(required_vars)))}"
        )

    def _invoke(self, prompt, callbacks: list | None = None) -> str:
        """Unified agent invocation method with timeout and exponential backoff.

        Uses exponential backoff based on total attempts, with different multipliers
        for different error types. This ensures backoff increases appropriately even
        when errors alternate between types.
        """
        max_retries = 5

        for attempt in range(max_retries):
            try:
                callback_list = callbacks or []
                # Always append monitoring callback - logging config controls output
                callback_list.append(MONITORING_CALLBACK)
                callback_list.append(self.agent_monitoring_callback)

                # Timeout: 5 minutes for first attempt, 10 minutes for retries
                timeout_seconds = 300 if attempt == 0 else 600

                logger.info(
                    f"Starting agent.invoke() [attempt {attempt + 1}/{max_retries}] with prompt length: {len(prompt)}, timeout: {timeout_seconds}s"
                )

                response = self._invoke_with_timeout(
                    timeout_seconds=timeout_seconds, callback_list=callback_list, prompt=prompt
                )

                logger.info(
                    f"Completed agent.invoke() - message count: {len(response['messages'])}, last message type: {type(response['messages'][-1])}"
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

            except TimeoutError as e:
                if attempt < max_retries - 1:
                    # Exponential backoff: 10s * 2^attempt (10s, 20s, 40s, 80s)
                    delay = min(10 * (2**attempt), 120)
                    logger.warning(
                        f"Agent invocation timed out after {timeout_seconds}s, retrying in {delay}s... (attempt {attempt + 1}/{max_retries})"
                    )
                    time.sleep(delay)
                else:
                    logger.error(f"Agent invocation timed out after {timeout_seconds}s on final attempt")
                    raise

            except ResourceExhausted as e:
                if attempt < max_retries - 1:
                    # Longer backoff for rate limits: 30s * 2^attempt (30s, 60s, 120s, 240s)
                    delay = min(30 * (2**attempt), 300)
                    logger.warning(
                        f"ResourceExhausted (rate limit): {e}\n"
                        f"Retrying in {delay}s... (attempt {attempt + 1}/{max_retries})"
                    )
                    time.sleep(delay)
                else:
                    logger.error(f"Max retries ({max_retries}) reached. ResourceExhausted: {e}")
                    raise

            except Exception as e:
                # Other errors (network, parsing, etc.) get standard exponential backoff
                if attempt < max_retries - 1:
                    delay = min(10 * (2**attempt), 120)
                    logger.warning(
                        f"Agent error: {type(e).__name__}: {e}, retrying in {delay}s... (attempt {attempt + 1}/{max_retries})"
                    )
                    time.sleep(delay)
                # On final attempt, fall through to return error message below

        logger.error("Max retries reached. Failed to get response from the agent.")
        return "Could not get response from the agent."

    def _invoke_with_timeout(self, timeout_seconds: int, callback_list: list, prompt: str):
        """Invoke agent with a timeout using threading."""
        import threading
        from queue import Queue, Empty

        result_queue: Queue = Queue()
        exception_queue: Queue = Queue()

        def invoke_target():
            try:
                response = self.agent.invoke(
                    {"messages": [self.system_message, HumanMessage(content=prompt)]},
                    config={"callbacks": callback_list, "recursion_limit": 40},
                )
                result_queue.put(response)
            except Exception as e:
                exception_queue.put(e)

        thread = threading.Thread(target=invoke_target, daemon=True)
        thread.start()
        thread.join(timeout=timeout_seconds)

        if thread.is_alive():
            # Thread is still running - timeout occurred
            logger.error(f"Agent invoke thread still running after {timeout_seconds}s timeout")
            raise TimeoutError(f"Agent invocation exceeded {timeout_seconds}s timeout")

        # Check for exceptions
        try:
            exception = exception_queue.get_nowait()
            raise exception
        except Empty:
            pass

        # Get result
        try:
            return result_queue.get_nowait()
        except Empty:
            raise RuntimeError("Agent invocation completed but no result was returned")

    def _parse_invoke(self, prompt, type):
        response = self._invoke(prompt)
        assert isinstance(response, str), f"Expected a string as response type got {response}"
        return self._parse_response(prompt, response, type)

    def _parse_response(self, prompt, response, return_type: Type[BaseModel], max_retries=5, attempt=0):
        if attempt >= max_retries:
            logger.error(f"Max retries ({max_retries}) reached for parsing response: {response}")
            raise Exception(f"Max retries reached for parsing response: {response}")

        if response is None or response.strip() == "":
            logger.error(f"Empty response for prompt: {prompt}")

        try:
            client = self._get_instructor_client()
            extraction_prompt = return_type.extractor_str() + response
            print("=== Extraction Prompt ===")
            print(extraction_prompt)
            create_kwargs = {
                "response_model": return_type,
                "messages": [{"role": "user", "content": extraction_prompt}],
                "max_retries": 2,
            }
            if self._instructor_model_name:
                create_kwargs["model"] = self._instructor_model_name
            result = client.chat.completions.create(**create_kwargs)
            return result
        except ValidationError as e:
            logger.warning(f"Validation error during extraction (attempt {attempt + 1}/{max_retries}): {e}")
            return self._parse_response(prompt, response, return_type, max_retries, attempt + 1)
        except ResourceExhausted as e:
            if attempt < max_retries - 1:
                delay = min(30 * (2**attempt), 300)
                logger.warning(
                    f"ResourceExhausted during parsing (rate limit): {e}\n"
                    f"Retrying in {delay}s... (attempt {attempt + 1}/{max_retries})"
                )
                time.sleep(delay)
                return self._parse_response(prompt, response, return_type, max_retries, attempt + 1)
            else:
                logger.error(f"Resource exhausted on final parsing attempt: {e}")
                raise


class LargeModelAgent(CodeBoardingAgent):
    def __init__(self, repo_dir: Path, static_analysis: StaticAnalysisResults, system_message: str):
        agent_model = os.getenv("AGENT_MODEL")
        llm, model_name = self._static_initialize_llm(model_override=agent_model, is_parsing=False)
        super().__init__(repo_dir, static_analysis, system_message, llm)
        self.agent_monitoring_callback.model_name = model_name
