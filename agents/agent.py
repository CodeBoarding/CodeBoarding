import json
import logging
from collections.abc import Callable
from pathlib import Path
from typing import Protocol, TypeVar

from google.api_core.exceptions import ResourceExhausted
from langchain_core.exceptions import OutputParserException
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import SystemMessage, HumanMessage, AIMessage, ToolMessage
from langchain_core.output_parsers import PydanticOutputParser
from langchain_core.prompts import PromptTemplate
from langchain.agents import create_agent
from langgraph.graph.state import CompiledStateGraph
from pydantic import ValidationError
from trustcall import create_extractor

from agents.prompts import get_validation_feedback_message
from agents.retry import RetryAction, RetryDecision, default_backoff, with_retries
from agents.tools.base import RepoContext
from agents.tools.toolkit import CodeBoardingToolkit
from agents.validation import ValidationResult, score_validation_results, VALIDATOR_WEIGHTS, DEFAULT_VALIDATOR_WEIGHT
from monitoring.mixin import MonitoringMixin
from repo_utils.ignore import RepoIgnoreManager
from agents.agent_responses import LLMBaseModel
from agents.llm_config import MONITORING_CALLBACK, current_provider_key_context
from agents.llm_errors import detect_auth_error
from static_analyzer.analysis_result import StaticAnalysisResults
from static_analyzer.reference_resolver import StaticReferenceResolver

logger = logging.getLogger(__name__)

ParseResultT = TypeVar("ParseResultT")
ResultT = TypeVar("ResultT", bound="RepairValidationResult")
RepairContextT = TypeVar("RepairContextT")
ValidationContextT = TypeVar("ValidationContextT")


class RepairValidationResult(Protocol):
    def llm_str(self) -> str: ...


class EmptyExtractorMessageError(ValueError):
    """Raised when extractor returns an empty message payload."""


def _raise_if_auth_error(exc: Exception) -> None:
    """Re-raise *exc* as a typed :class:`LLMAuthError` when it's a rejected key.

    Shared by the retry classifiers in ``_invoke`` and ``_parse_response``: a
    401 is permanent for the run, so raising here propagates it terminally (the
    retry loop never sees a RETRY decision) and the CLI can surface an
    actionable message. Non-auth exceptions fall through untouched.
    """
    provider, key_tail = current_provider_key_context()
    auth_error = detect_auth_error(exc, provider=provider, key_tail=key_tail)
    if auth_error is not None:
        logger.error("LLM auth failure — not retrying: %s", auth_error)
        raise auth_error from exc


class CodeBoardingAgent(MonitoringMixin):
    def __init__(
        self,
        repo_dir: Path,
        static_analysis: StaticAnalysisResults,
        system_message: str,
        agent_llm: BaseChatModel,
        parsing_llm: BaseChatModel,
    ):
        MonitoringMixin.__init__(self)
        self.parsing_llm = parsing_llm
        self.agent_llm = agent_llm
        self.repo_dir = repo_dir
        self.static_analysis = static_analysis
        self.reference_resolver = StaticReferenceResolver(repo_dir, static_analysis)
        self.ignore_manager = RepoIgnoreManager(repo_dir)

        context = RepoContext(repo_dir=repo_dir, ignore_manager=self.ignore_manager, static_analysis=static_analysis)
        self.toolkit = CodeBoardingToolkit(context=context)

        self.agent: CompiledStateGraph = create_agent(
            model=agent_llm,
            tools=self.toolkit.get_agent_tools(),
        )
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
    def component_bridge_edges_tool(self):
        return self.toolkit.component_bridge_edges

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

        Classification applied per exception:
        - ``TimeoutError``: backoff ``min(10·2^n, 120)``, raise on exhaustion.
        - ``ResourceExhausted``: backoff ``min(30·2^n, 300)``, raise on exhaustion.
        - ``status_code == 404``: raise immediately (retired model ID, etc.).
        - Other exceptions: backoff ``min(10·2^n, 120)``, return fallback string
          on exhaustion (non-raising — callers treat the fallback as a failed run).
        """
        max_attempts = 5
        # Counter captured by the closure so we can vary the per-attempt timeout
        # without reaching into the retry helper.
        attempt_counter = [0]

        def call_once() -> str:
            attempt = attempt_counter[0]
            attempt_counter[0] += 1
            timeout_seconds = 300 if attempt == 0 else 600
            callback_list = (callbacks or []) + [MONITORING_CALLBACK, self.agent_monitoring_callback]
            logger.info(
                f"Starting agent.invoke() [attempt {attempt + 1}/{max_attempts}] with prompt length: {len(prompt)}, timeout: {timeout_seconds}s"
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
                return "".join(str(m) if not isinstance(m, str) else m for m in agent_response.content)
            return ""  # unreachable for AIMessage but satisfies typing

        def classify(exc: Exception, attempt: int) -> RetryDecision:
            _raise_if_auth_error(exc)
            if getattr(exc, "status_code", None) == 404:
                logger.error(f"Permanent HTTP 404 — not retrying: {type(exc).__name__}: {exc}")
                return RetryDecision(action=RetryAction.GIVE_UP)
            if isinstance(exc, ResourceExhausted):
                return RetryDecision(
                    action=RetryAction.RETRY,
                    backoff_s=default_backoff(attempt, initial_s=30.0, multiplier=2.0, max_s=300.0),
                )
            # TimeoutError + generic Exception share the same backoff.
            return RetryDecision(
                action=RetryAction.RETRY,
                backoff_s=default_backoff(attempt, initial_s=10.0, multiplier=2.0, max_s=120.0),
            )

        def on_exhausted(exc: Exception) -> str:
            # Typed exceptions surface the original error; only generic falls through
            # to the historic fallback string that callers have long relied on.
            if isinstance(exc, (TimeoutError, ResourceExhausted)):
                raise exc
            return "Could not get response from the agent."

        return with_retries(
            call_once,
            max_attempts=max_attempts,
            classify=classify,
            on_exhausted=on_exhausted,
            log_prefix="Agent invocation",
        )

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

    def _parse_invoke(
        self,
        prompt: str,
        return_type: type[ParseResultT],
        include_hidden: bool = False,
    ) -> ParseResultT:
        response = self._invoke(prompt)
        assert isinstance(response, str), f"Expected a string as response type got {response}"
        return self._parse_response(prompt, response, return_type, include_hidden=include_hidden)

    def _repair_result(
        self,
        result: ResultT,
        repairs: list[Callable[[ResultT, RepairContextT], None]],
        repair_context: RepairContextT,
    ) -> None:
        """Apply deterministic repairs to one parsed candidate."""
        for repair in repairs:
            logger.info("[Repair] Applying %s", repair.__name__)
            repair(result, repair_context)

    def _score_result(
        self,
        result: ResultT,
        validators: list[Callable[[ResultT, ValidationContextT], ValidationResult]],
        validation_context: ValidationContextT,
    ) -> tuple[float, list[tuple[float, str]]]:
        """Run all validators on a result and return (score, prioritized_feedback).

        The score is computed using weighted validators where coverage-related
        validators (cluster coverage, group name coverage) carry significantly
        more weight than others.

        Feedback messages are returned as (weight, message) tuples sorted by
        weight descending, so that the LLM focuses on the most critical issues
        (cluster/group coverage) before lower-priority ones (key entities).
        """
        validator_results: list[tuple[Callable[[ResultT, ValidationContextT], ValidationResult], ValidationResult]] = []
        weighted_feedback: list[tuple[float, str]] = []
        for validator in validators:
            validator_result: ValidationResult = validator(result, validation_context)
            validator_results.append((validator, validator_result))
            if not validator_result.is_valid:
                weight = VALIDATOR_WEIGHTS.get(validator.__name__, DEFAULT_VALIDATOR_WEIGHT)
                for msg in validator_result.feedback_messages:
                    weighted_feedback.append((weight, msg))

        # Sort by weight descending so critical feedback comes first
        weighted_feedback.sort(key=lambda x: x[0], reverse=True)

        score = score_validation_results(validator_results)
        return score, weighted_feedback

    def _invoke_repair_validate(
        self,
        prompt: str,
        return_type: type[ResultT],
        repairs: list[Callable[[ResultT, RepairContextT], None]],
        validators: list[Callable[[ResultT, ValidationContextT], ValidationResult]],
        repair_context: RepairContextT,
        validation_context: ValidationContextT,
        max_validation_attempts: int = 1,
        include_hidden: bool = False,
    ) -> ResultT:
        """Bind deterministic repairs to every candidate before validation."""

        def repair_candidate(result: ResultT) -> None:
            self._repair_result(result, repairs, repair_context)

        return self._invoke_validate(
            prompt,
            return_type,
            validators,
            validation_context,
            max_validation_attempts=max_validation_attempts,
            include_hidden=include_hidden,
            candidate_repair=repair_candidate,
        )

    def _invoke_validate(
        self,
        prompt: str,
        return_type: type[ResultT],
        validators: list[Callable[[ResultT, ValidationContextT], ValidationResult]],
        validation_context: ValidationContextT,
        max_validation_attempts: int = 1,
        include_hidden: bool = False,
        candidate_repair: Callable[[ResultT], None] | None = None,
    ) -> ResultT:
        """Validate parsed candidates and return the best-scoring result."""
        # Compute the maximum possible score so we can detect a perfect result
        max_possible_score = sum(VALIDATOR_WEIGHTS.get(v.__name__, DEFAULT_VALIDATOR_WEIGHT) for v in validators)

        result = self._parse_invoke(prompt, return_type, include_hidden=include_hidden)
        if candidate_repair is not None:
            candidate_repair(result)
        logger.info(
            "[Validation] Parsed%s %s: %s",
            " and repaired" if candidate_repair is not None else "",
            return_type.__name__,
            result.llm_str()[:500],
        )

        # Track the best candidate across all attempts
        best_result = result
        best_score = -1.0

        # Weight threshold: validators above this are tagged [CRITICAL]
        critical_threshold = 10.0

        for attempt in range(1, max_validation_attempts + 1):
            score, weighted_feedback = self._score_result(result, validators, validation_context)

            logger.info(
                f"[Validation] Attempt {attempt}/{max_validation_attempts} "
                f"score: {score}/{max_possible_score} "
                f"({len(weighted_feedback)} issue(s))"
            )

            if score > best_score:
                best_score = score
                best_result = result

            # Perfect score — return immediately
            if score >= max_possible_score:
                logger.info(f"[Validation] Perfect score on attempt {attempt}, returning result")
                return result

            # On the last attempt, don't retry — just fall through to return best
            if attempt == max_validation_attempts:
                logger.warning(
                    f"[Validation] Final attempt reached. Best score: {best_score}/{max_possible_score}. "
                    f"Returning best result."
                )
                break

            # Build feedback prompt for the next attempt.
            # Feedback is sorted by weight; high-weight items are tagged [CRITICAL].
            feedback_lines: list[str] = []
            for weight, msg in weighted_feedback:
                tag = "CRITICAL" if weight >= critical_threshold else "Secondary"
                feedback_lines.append(f"- [{tag}] {msg}")

            feedback_template = get_validation_feedback_message()
            feedback_prompt = feedback_template.format(
                original_output=result.llm_str(),
                feedback_list="\n".join(feedback_lines),
                original_prompt=prompt,
            )

            logger.info(
                f"[Validation] Preparing attempt {attempt + 1}/{max_validation_attempts} "
                f"with {len(weighted_feedback)} feedback items"
            )
            result = self._parse_invoke(feedback_prompt, return_type, include_hidden=include_hidden)
            if candidate_repair is not None:
                candidate_repair(result)

        return best_result

    def _parse_response(self, prompt, response, return_type, max_retries=5, attempt=0, include_hidden: bool = False):
        if response is None or response.strip() == "":
            logger.error(f"Empty response for prompt: {prompt}")

        if include_hidden and issubclass(return_type, LLMBaseModel):
            schema = return_type.model_json_schema(include_hidden=True)
            parser = PydanticOutputParser(pydantic_object=return_type)
            format_instructions = (
                f"The output should be formatted as a JSON instance that conforms to the JSON schema below.\n"
                f"Here is the output schema:\n```json\n{json.dumps(schema, indent=2)}\n```"
            )
        else:
            parser = PydanticOutputParser(pydantic_object=return_type)
            format_instructions = parser.get_format_instructions()

        def call_once():
            try:
                result = self._structured_parse(response, parser, format_instructions=format_instructions)
                logger.debug("[parse_response] structured_parse succeeded for %s", return_type.__name__)
                return result
            except Exception as e:
                logger.warning("[parse_response] structured_parse failed for %s: %s", return_type.__name__, e)
            return self._extractor_parse(response, return_type, parser, include_hidden=include_hidden)

        def classify(exc: Exception, attempt: int) -> RetryDecision:
            _raise_if_auth_error(exc)
            if isinstance(exc, ResourceExhausted):
                return RetryDecision(
                    action=RetryAction.RETRY,
                    backoff_s=default_backoff(attempt, initial_s=30.0, multiplier=2.0, max_s=300.0),
                )
            if isinstance(exc, (EmptyExtractorMessageError, IndexError, json.JSONDecodeError, ValueError)):
                return RetryDecision(action=RetryAction.RETRY_NOW)
            return RetryDecision(action=RetryAction.GIVE_UP)

        def on_exhausted(exc: Exception):
            if isinstance(exc, ResourceExhausted):
                logger.error(f"Resource exhausted on final parsing attempt: {exc}")
                raise exc
            logger.error(f"Max retries ({max_retries}) reached for parsing response: {response}")
            raise Exception(f"Max retries reached for parsing response: {response}")

        return with_retries(
            call_once,
            max_attempts=max(1, max_retries - attempt),
            classify=classify,
            on_exhausted=on_exhausted,
            log_prefix="Parse response",
        )

    def _structured_parse(self, message_content, parser, format_instructions: str | None = None):
        if format_instructions is None:
            format_instructions = parser.get_format_instructions()
        prompt_template = """You are a JSON expert. Here you need to extract information in the following json format: {format_instructions}

        Here is the content to parse and fix: {adjective}

        Please provide only the JSON output without any additional text."""
        prompt = PromptTemplate(
            template=prompt_template,
            input_variables=["adjective"],
            partial_variables={"format_instructions": format_instructions},
        )
        chain = prompt | self.parsing_llm | parser
        try:
            return chain.invoke(
                {"adjective": message_content},
                config={"callbacks": [MONITORING_CALLBACK, self.agent_monitoring_callback]},
            )
        except (ValidationError, OutputParserException):
            for _, v in json.loads(message_content).items():
                try:
                    return self._structured_parse(json.dumps(v), parser)
                except:
                    pass
        raise ValueError(f"Couldn't parse {message_content}")

    def _extractor_parse(self, response, return_type, parser, include_hidden: bool = False):
        extractor = create_extractor(self.parsing_llm, tools=[return_type], tool_choice=return_type.__name__)
        try:
            result = extractor.invoke(
                return_type.extractor_str(include_hidden=include_hidden) + response,
                config={"callbacks": [MONITORING_CALLBACK, self.agent_monitoring_callback]},
            )
        except AttributeError as e:
            if "tool_call_id" in str(e):
                logger.warning(f"Trustcall bug encountered: {e}")
                raise
            raise
        if "responses" in result and len(result["responses"]) != 0:
            return return_type.model_validate(result["responses"][0])
        if "messages" in result and len(result["messages"]) != 0:
            message = result["messages"][0].content
            if not message:
                raise EmptyExtractorMessageError("Extractor returned empty message content")
            return self._structured_parse(message, parser)
        raise EmptyExtractorMessageError("Extractor returned no responses and no messages")
