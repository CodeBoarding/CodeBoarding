import json
import logging
import time
from pathlib import Path

from google.api_core.exceptions import ResourceExhausted
from langchain_core.exceptions import OutputParserException
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import SystemMessage, HumanMessage, AIMessage
from langchain_core.output_parsers import PydanticOutputParser
from langchain_core.prompts import PromptTemplate
from langchain.agents import create_agent
from langgraph.graph.state import CompiledStateGraph
from pydantic import BaseModel, ValidationError
from trustcall import create_extractor

from agents.prompts import get_validation_feedback_message
from agents.tools.base import RepoContext
from agents.tools.toolkit import CodeBoardingToolkit
from monitoring.mixin import MonitoringMixin
from repo_utils.ignore import RepoIgnoreManager
from agents.llm_config import MONITORING_CALLBACK
from static_analyzer.analysis_result import StaticAnalysisResults
from static_analyzer.reference_resolve_mixin import ReferenceResolverMixin
from agents.validation import ValidationIssue

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

        Uses exponential backoff based on total attempts, with different multipliers
        for different error types. This ensures backoff increases appropriately even
        when errors alternate between types.
        """
        max_retries = 5

        for attempt in range(max_retries):
            timeout_seconds = 300 if attempt == 0 else 600
            try:
                callback_list = callbacks or []
                # Always append monitoring callback - logging config controls output
                callback_list.append(MONITORING_CALLBACK)
                callback_list.append(self.agent_monitoring_callback)

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

    def _parse_invoke(self, prompt: str, type: type):
        response = self._invoke(prompt)
        assert isinstance(response, str), f"Expected a string as response type got {response}"
        return self._parse_response(prompt, response, type)

    def _run_validators(self, result, validators: list, context) -> list[ValidationIssue]:
        """Run validators and flatten machine-readable issues."""
        issues: list[ValidationIssue] = []

        for validator in validators:
            validation_result = validator(result, context)
            if validation_result.is_valid:
                continue

            if validation_result.issues:
                issues.extend(validation_result.issues)
                continue

            issues.extend(
                ValidationIssue(code=validator.__name__, message=message)
                for message in validation_result.feedback_messages
            )

        return issues

    def _serialize_result_for_retry(self, result) -> str:
        """Serialize the current result for schema-preserving repair prompts."""
        if isinstance(result, BaseModel):
            return result.model_dump_json(indent=2, exclude_none=True)
        return json.dumps(result, indent=2, default=str)

    def _try_programmatic_repairs(self, result, issues: list[ValidationIssue], context) -> tuple[object, bool]:
        """Attempt deterministic repairs before falling back to an LLM retry."""
        if not issues:
            return result, False

        repaired = False
        missing_cluster_ids: set[int] = set()
        for issue in issues:
            if issue.code != "missing_cluster_ids":
                continue
            cluster_ids = issue.payload.get("cluster_ids", [])
            if isinstance(cluster_ids, list):
                missing_cluster_ids.update(cid for cid in cluster_ids if isinstance(cid, int))

        if missing_cluster_ids and hasattr(self, "_auto_assign_missing_clusters"):
            updated_result, unresolved = self._auto_assign_missing_clusters(
                cluster_analysis=result,
                expected_cluster_ids=context.expected_cluster_ids,
                cluster_results=context.cluster_results,
                cfg_graphs=context.cfg_graphs or None,
            )
            if len(unresolved) < len(missing_cluster_ids):
                repaired = True
                result = updated_result
                logger.info(
                    f"[Validation] Programmatic repair resolved {len(missing_cluster_ids) - len(unresolved)} "
                    f"cluster issue(s) before retry"
                )

        return result, repaired

    def _build_targeted_retry_prompt(self, prompt: str, result, issues: list[ValidationIssue]) -> str:
        """Build a retry prompt that preserves the current schema and narrows repair scope."""
        feedback_template = get_validation_feedback_message()
        feedback_lines = [f"- [{issue.code}] {issue.message}" for issue in issues]
        retry_scope = "mixed"
        unique_codes = {issue.code for issue in issues}
        if len(unique_codes) == 1:
            retry_scope = next(iter(unique_codes))
        scoped_prompt = (
            f"Retry scope: `{retry_scope}`.\n"
            f"Return the corrected object in the exact same schema. Do not rename fields. "
            f"Do not remove valid items. Only apply the minimum necessary edits.\n\n"
            f"{prompt}"
        )
        return feedback_template.format(
            original_output=self._serialize_result_for_retry(result),
            feedback_list="\n".join(feedback_lines),
            original_prompt=scoped_prompt,
        )

    def _validation_invoke(
        self, prompt: str, return_type: type, validators: list, context, max_validation_retries: int = 1
    ):
        """
        Invoke LLM with validation and feedback loop.

        Args:
            prompt: The original prompt
            return_type: Pydantic type to parse into
            validators: List of validation functions to run
            context: ValidationContext with data needed for validation
            max_validation_retries: Maximum retry attempts with feedback (default: 1)

        Returns:
            Validated result of return_type
        """
        result = self._parse_invoke(prompt, return_type)

        for attempt in range(1, max_validation_retries + 1):
            issues = self._run_validators(result, validators, context)
            if not issues:
                logger.info(f"[Validation] All validations passed on attempt {attempt}")
                return result

            result, repaired = self._try_programmatic_repairs(result, issues, context)
            if repaired:
                issues = self._run_validators(result, validators, context)
                if not issues:
                    logger.info(f"[Validation] All validations passed after programmatic repair on attempt {attempt}")
                    return result

            if attempt == max_validation_retries:
                logger.warning(
                    f"[Validation] Still {len(issues)} issue(s) after {max_validation_retries} retries, "
                    f"returning best result"
                )
                return result

            logger.info(f"[Validation] Retry {attempt}/{max_validation_retries} with {len(issues)} feedback items")
            feedback_prompt = self._build_targeted_retry_prompt(prompt, result, issues)
            result = self._parse_invoke(feedback_prompt, return_type)

        return result

    def _parse_response(self, prompt, response, return_type, max_retries=5, attempt=0):
        if attempt >= max_retries:
            logger.error(f"Max retries ({max_retries}) reached for parsing response: {response}")
            raise Exception(f"Max retries reached for parsing response: {response}")

        extractor = create_extractor(self.parsing_llm, tools=[return_type], tool_choice=return_type.__name__)
        if response is None or response.strip() == "":
            logger.error(f"Empty response for prompt: {prompt}")
        try:
            result = extractor.invoke(
                return_type.extractor_str() + response,
                config={"callbacks": [MONITORING_CALLBACK, self.agent_monitoring_callback]},
            )
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
        except EmptyExtractorMessageError as e:
            logger.warning(f"{e} (attempt {attempt + 1}/{max_retries})")
            return self._parse_response(prompt, response, return_type, max_retries, attempt + 1)
        except AttributeError as e:
            # Workaround for trustcall bug: https://github.com/hinthornw/trustcall/issues/47
            # 'ExtractionState' object has no attribute 'tool_call_id' occurs during validation retry
            if "tool_call_id" in str(e):
                logger.warning(f"Trustcall bug encountered, falling back to Pydantic parser: {e}")
                parser = PydanticOutputParser(pydantic_object=return_type)
                return self._try_parse(response, parser)
            raise
        except IndexError as e:
            # try to parse with the json parser if possible
            logger.warning(f"IndexError while parsing response (attempt {attempt + 1}/{max_retries}): {e}")
            return self._parse_response(prompt, response, return_type, max_retries, attempt + 1)
        except ResourceExhausted as e:
            # Parsing uses exponential backoff for rate limits
            if attempt < max_retries - 1:
                # Exponential backoff: 30s * 2^attempt, capped at 300s
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
