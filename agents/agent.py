import json
import logging
from pathlib import Path

from google.api_core.exceptions import ResourceExhausted
from langchain_core.exceptions import OutputParserException
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import SystemMessage, HumanMessage, AIMessage
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
from agents.llm_config import MONITORING_CALLBACK
from static_analyzer.analysis_result import StaticAnalysisResults
from static_analyzer.reference_resolve_mixin import ReferenceResolverMixin

logger = logging.getLogger(__name__)


class EmptyExtractorMessageError(ValueError):
    """Raised when extractor returns an empty message payload."""


class CodeBoardingAgent(ReferenceResolverMixin, MonitoringMixin):
    def __init__(
        self,
        repo_dir: Path,
        static_analysis: StaticAnalysisResults,
        system_message: str,
        agent_llm: BaseChatModel,
        parsing_llm: BaseChatModel,
    ):
        ReferenceResolverMixin.__init__(self, repo_dir, static_analysis)
        MonitoringMixin.__init__(self)
        self.parsing_llm = parsing_llm
        self.repo_dir = repo_dir
        self.ignore_manager = RepoIgnoreManager(repo_dir)

        # Initialize the professional toolkit
        context = RepoContext(repo_dir=repo_dir, ignore_manager=self.ignore_manager, static_analysis=static_analysis)
        self.toolkit = CodeBoardingToolkit(context=context)

        self.agent: CompiledStateGraph = create_agent(
            model=agent_llm,
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

    def _parse_invoke(self, prompt: str, type: type):
        response = self._invoke(prompt)
        assert isinstance(response, str), f"Expected a string as response type got {response}"
        return self._parse_response(prompt, response, type)

    def _score_result(self, result, validators: list, context) -> tuple[float, list[tuple[float, str]]]:
        """Run all validators on a result and return (score, prioritized_feedback).

        The score is computed using weighted validators where coverage-related
        validators (cluster coverage, group name coverage) carry significantly
        more weight than others.

        Feedback messages are returned as (weight, message) tuples sorted by
        weight descending, so that the LLM focuses on the most critical issues
        (cluster/group coverage) before lower-priority ones (key entities).
        """
        validator_results: list[tuple] = []
        weighted_feedback: list[tuple[float, str]] = []
        for validator in validators:
            validator_result: ValidationResult = validator(result, context)
            validator_results.append((validator, validator_result))
            if not validator_result.is_valid:
                weight = VALIDATOR_WEIGHTS.get(validator.__name__, DEFAULT_VALIDATOR_WEIGHT)
                for msg in validator_result.feedback_messages:
                    weighted_feedback.append((weight, msg))

        # Sort by weight descending so critical feedback comes first
        weighted_feedback.sort(key=lambda x: x[0], reverse=True)

        score = score_validation_results(validator_results)
        return score, weighted_feedback

    def _validation_invoke(
        self, prompt: str, return_type: type, validators: list, context, max_validation_attempts: int = 1
    ):
        """
        Invoke LLM with validation, feedback loop, and best-of-N selection.

        Each attempt (initial + retries) is scored using weighted validators.
        Coverage validators (validate_cluster_coverage, validate_group_name_coverage)
        are weighted ~2x higher than other validators, so the selection strongly
        favours results with complete coverage.

        If any attempt scores perfectly (all validators pass), it is returned
        immediately. Otherwise the highest-scoring result across all attempts is
        returned.

        Args:
            prompt: The original prompt
            return_type: Pydantic type to parse into
            validators: List of validation functions to run
            context: ValidationContext with data needed for validation
            max_validation_attempts: Maximum validation attempts (initial attempt included).
                Retries occur only when this value is greater than 1. (default: 1)

        Returns:
            The highest-scoring result of return_type across all attempts
        """
        # Compute the maximum possible score so we can detect a perfect result
        max_possible_score = sum(VALIDATOR_WEIGHTS.get(v.__name__, DEFAULT_VALIDATOR_WEIGHT) for v in validators)

        result = self._parse_invoke(prompt, return_type)

        # Track the best candidate across all attempts
        best_result = result
        best_score = -1.0

        # Weight threshold: validators above this are tagged [CRITICAL]
        critical_threshold = 10.0

        for attempt in range(1, max_validation_attempts + 1):
            score, weighted_feedback = self._score_result(result, validators, context)

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
            result = self._parse_invoke(feedback_prompt, return_type)

        return best_result

    def _parse_response(self, prompt, response, return_type, max_retries=5, attempt=0):
        if response is None or response.strip() == "":
            logger.error(f"Empty response for prompt: {prompt}")

        def call_once():
            # Extractor is rebuilt on every attempt — previous trustcall state
            # may have corrupted attributes (see the tool_call_id bug below).
            extractor = create_extractor(self.parsing_llm, tools=[return_type], tool_choice=return_type.__name__)
            try:
                result = extractor.invoke(
                    return_type.extractor_str() + response,
                    config={"callbacks": [MONITORING_CALLBACK, self.agent_monitoring_callback]},
                )
            except AttributeError as e:
                # Trustcall bug: https://github.com/hinthornw/trustcall/issues/47
                # 'ExtractionState' object has no attribute 'tool_call_id' during validation retry.
                # Treat as a non-retriable fallback to the Pydantic parser.
                if "tool_call_id" in str(e):
                    logger.warning(f"Trustcall bug encountered, falling back to Pydantic parser: {e}")
                    parser = PydanticOutputParser(pydantic_object=return_type)
                    return self._try_parse(response, parser)
                raise
            if "responses" in result and len(result["responses"]) != 0:
                return return_type.model_validate(result["responses"][0])
            if "messages" in result and len(result["messages"]) != 0:
                message = result["messages"][0].content
                parser = PydanticOutputParser(pydantic_object=return_type)
                if not message:
                    raise EmptyExtractorMessageError("Extractor returned empty message content")
                return self._try_parse(message, parser)
            parser = PydanticOutputParser(pydantic_object=return_type)
            return self._try_parse(response, parser)

        def classify(exc: Exception, attempt: int) -> RetryDecision:
            if isinstance(exc, ResourceExhausted):
                return RetryDecision(
                    action=RetryAction.RETRY,
                    backoff_s=default_backoff(attempt, initial_s=30.0, multiplier=2.0, max_s=300.0),
                )
            if isinstance(exc, (EmptyExtractorMessageError, IndexError, json.JSONDecodeError, ValueError)):
                return RetryDecision(action=RetryAction.RETRY_NOW)
            # AttributeError (non-tool_call_id) and any other exception: give up.
            return RetryDecision(action=RetryAction.GIVE_UP)

        def on_exhausted(exc: Exception):
            # Preserve historic shape: ResourceExhausted surfaces the original exception;
            # parse-error exhaustion wraps with a descriptive message naming the response.
            if isinstance(exc, ResourceExhausted):
                logger.error(f"Resource exhausted on final parsing attempt: {exc}")
                raise exc
            logger.error(f"Max retries ({max_retries}) reached for parsing response: {response}")
            raise Exception(f"Max retries reached for parsing response: {response}")

        # ``attempt`` kwarg kept for backwards-compat with callers that passed it;
        # the effective attempt count is ``max_retries - attempt``.
        return with_retries(
            call_once,
            max_attempts=max(1, max_retries - attempt),
            classify=classify,
            on_exhausted=on_exhausted,
            log_prefix="Parse response",
        )

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
            chain = prompt | self.parsing_llm | parser
            return chain.invoke(
                {"adjective": message_content},
                config={"callbacks": [MONITORING_CALLBACK, self.agent_monitoring_callback]},
            )
        except (ValidationError, OutputParserException):
            for _, v in json.loads(message_content).items():
                try:
                    return self._try_parse(json.dumps(v), parser)
                except:
                    pass
        raise ValueError(f"Couldn't parse {message_content}")
