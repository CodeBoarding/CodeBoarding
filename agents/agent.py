import logging
import os
import time
from pathlib import Path
from typing import Type, TypeVar

import instructor
from google.api_core.exceptions import ResourceExhausted
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import SystemMessage, HumanMessage, AIMessage
from langgraph.prebuilt import create_react_agent
from pydantic import ValidationError

from agents.agent_responses import LLMBaseModel
from agents.llm_config import create_llm_from_env, create_instructor_client_from_env
from agents.tools.base import RepoContext
from agents.tools.toolkit import CodeBoardingToolkit
from monitoring.callbacks import MonitoringCallback
from monitoring.mixin import MonitoringMixin
from repo_utils.ignore import RepoIgnoreManager
from static_analyzer.analysis_result import StaticAnalysisResults
from static_analyzer.reference_resolve_mixin import ReferenceResolverMixin

LLMResponseT = TypeVar("LLMResponseT", bound=LLMBaseModel)

logger = logging.getLogger(__name__)

# Initialize global monitoring callback with its own stats container to avoid ContextVar dependency
from monitoring.stats import RunStats

MONITORING_CALLBACK = MonitoringCallback(stats_container=RunStats())


class CodeBoardingAgent(ReferenceResolverMixin, MonitoringMixin):
    def __init__(
        self,
        repo_dir: Path,
        static_analysis: StaticAnalysisResults,
        system_message: str,
        llm: BaseChatModel,
        model_name: str,
        instructor_client: instructor.Instructor,
        instructor_model_name: str,
    ):
        ReferenceResolverMixin.__init__(self, repo_dir, static_analysis)
        MonitoringMixin.__init__(self)
        self.llm = llm
        self.model_name = model_name
        self.instructor_client = instructor_client
        self.instructor_model_name = instructor_model_name
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

    def _parse_invoke(self, prompt: str, response_type: Type[LLMResponseT]) -> LLMResponseT:
        response = self._invoke(prompt)
        assert isinstance(response, str), f"Expected a string as response type got {response}"
        return self._parse_response(prompt, response, response_type)

    def _parse_response(
        self, prompt: str, response: str, return_type: Type[LLMResponseT], max_retries: int = 5, attempt: int = 0
    ) -> LLMResponseT:
        if attempt >= max_retries:
            logger.error(f"Max retries ({max_retries}) reached for parsing response: {response}")
            raise Exception(f"Max retries reached for parsing response: {response}")

        if response is None or response.strip() == "":
            logger.error(f"Empty response for prompt: {prompt}")

        try:
            extraction_prompt = return_type.extractor_str() + response
            create_kwargs = {
                "response_model": return_type,
                "messages": [{"role": "user", "content": extraction_prompt}],
                "max_retries": 2,
            }
            if self.instructor_model_name:
                create_kwargs["model"] = self.instructor_model_name
            result = self.instructor_client.chat.completions.create(**create_kwargs)
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
        llm, model_name, _ = create_llm_from_env(model_override=agent_model, is_parsing=False)
        instructor_client, instructor_model_name = create_instructor_client_from_env()

        MONITORING_CALLBACK.model_name = model_name

        super().__init__(
            repo_dir=repo_dir,
            static_analysis=static_analysis,
            system_message=system_message,
            llm=llm,
            model_name=model_name,
            instructor_client=instructor_client,
            instructor_model_name=instructor_model_name,
        )
        self.agent_monitoring_callback.model_name = model_name
