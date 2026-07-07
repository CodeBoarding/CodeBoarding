import os
import shutil
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, MagicMock, patch

from langchain_core.messages import AIMessage
from langchain_core.language_models import BaseChatModel
from pydantic import BaseModel

from agents.agent import (
    CodeBoardingAgent,
    _AgentInvocationCancelled,
    _AgentInvocationStillRunning,
    _CancellationCallback,
)
from static_analyzer.analysis_result import StaticAnalysisResults
from monitoring.stats import RunStats, current_stats


class TestResponse(BaseModel):
    """Test response model for parsing tests"""

    value: str

    @staticmethod
    def extractor_str(include_hidden: bool = False):
        return "Extract the value field: "


class TestCodeBoardingAgent(unittest.TestCase):
    def setUp(self):
        # Create temporary directory
        self.temp_dir = tempfile.mkdtemp()
        self.repo_dir = Path(self.temp_dir)

        # Create mock static analysis
        self.mock_analysis = Mock(spec=StaticAnalysisResults)
        self.mock_analysis.call_graph = Mock()
        self.mock_analysis.class_hierarchies = {}
        self.mock_analysis.package_relations = {}
        self.mock_analysis.references = []

        # Mock environment variables
        self.env_patcher = patch.dict(os.environ, {"OPENAI_API_KEY": "test_key", "PARSING_MODEL": "gpt-4o"}, clear=True)
        self.env_patcher.start()

        # Set up monitoring context
        self.run_stats = RunStats()
        self.token = current_stats.set(self.run_stats)
        self.mock_llm = MagicMock(spec=BaseChatModel)

    def tearDown(self):
        # Clean up
        shutil.rmtree(self.temp_dir, ignore_errors=True)
        self.env_patcher.stop()

        # Reset monitoring context
        current_stats.reset(self.token)

    def _create_agent_with_executor(self, mock_create_agent, mock_agent_executor):
        mock_create_agent.return_value = mock_agent_executor
        mock_parsing_llm = Mock(spec=BaseChatModel)
        return CodeBoardingAgent(
            repo_dir=self.repo_dir,
            static_analysis=self.mock_analysis,
            system_message="Test",
            agent_llm=self.mock_llm,
            parsing_llm=mock_parsing_llm,
        )

    @patch("agents.llm_config.LLM_PROVIDERS")
    @patch("agents.agent.create_agent")
    def test_init_with_openai(self, mock_create_agent, mock_providers):
        # Test initialization with OpenAI
        mock_llm = Mock(spec=BaseChatModel)
        mock_parsing_llm = Mock(spec=BaseChatModel)
        mock_agent_executor = Mock()
        mock_create_agent.return_value = mock_agent_executor

        agent = CodeBoardingAgent(
            repo_dir=self.repo_dir,
            static_analysis=self.mock_analysis,
            system_message="Test system message",
            agent_llm=mock_llm,
            parsing_llm=mock_parsing_llm,
        )

        # Verify agent was created
        mock_create_agent.assert_called_once()
        # Verify attributes
        self.assertEqual(agent.repo_dir, self.repo_dir)
        self.assertEqual(agent.static_analysis, self.mock_analysis)
        self.assertEqual(agent.parsing_llm, mock_parsing_llm)

    @patch("agents.agent.create_agent")
    def test_init_direct(self, mock_create_agent):
        # Test direct initialization with mocked LLMs
        mock_llm = Mock(spec=BaseChatModel)
        mock_parsing_llm = Mock(spec=BaseChatModel)
        mock_create_agent.return_value = Mock()

        agent = CodeBoardingAgent(
            repo_dir=self.repo_dir,
            static_analysis=self.mock_analysis,
            system_message="Test",
            agent_llm=mock_llm,
            parsing_llm=mock_parsing_llm,
        )
        self.assertIsNotNone(agent)
        self.assertEqual(agent.parsing_llm, mock_parsing_llm)

    @patch("agents.agent.create_agent")
    def test_invoke_success(self, mock_create_agent):
        # Test successful invocation
        mock_agent_executor = Mock()
        mock_create_agent.return_value = mock_agent_executor

        # Mock agent response
        mock_response_message = AIMessage(content="Test response")
        mock_agent_executor.invoke.return_value = {"messages": [mock_response_message]}

        mock_parsing_llm = Mock(spec=BaseChatModel)
        agent = CodeBoardingAgent(
            repo_dir=self.repo_dir,
            static_analysis=self.mock_analysis,
            system_message="Test",
            agent_llm=self.mock_llm,
            parsing_llm=mock_parsing_llm,
        )

        result = agent._invoke("Test prompt")

        self.assertEqual(result, "Test response")
        mock_agent_executor.invoke.assert_called_once()

    @patch("agents.agent.create_agent")
    def test_invoke_with_list_content(self, mock_create_agent):
        # Test invocation with list content response
        mock_agent_executor = Mock()
        mock_create_agent.return_value = mock_agent_executor

        # Mock agent response with list content
        mock_response_message = AIMessage(content=["Part 1", "Part 2"])
        mock_agent_executor.invoke.return_value = {"messages": [mock_response_message]}

        mock_parsing_llm = Mock(spec=BaseChatModel)
        agent = CodeBoardingAgent(
            repo_dir=self.repo_dir,
            static_analysis=self.mock_analysis,
            system_message="Test",
            agent_llm=self.mock_llm,
            parsing_llm=mock_parsing_llm,
        )

        result = agent._invoke("Test prompt")

        self.assertEqual(result, "Part 1Part 2")

    @patch("agents.agent.create_agent")
    @patch("time.sleep")
    def test_invoke_with_retry(self, mock_sleep, mock_create_agent):
        # Test invocation with retry on ResourceExhausted
        from google.api_core.exceptions import ResourceExhausted

        mock_agent_executor = Mock()
        mock_create_agent.return_value = mock_agent_executor

        # First call raises exception, second succeeds
        mock_response_message = AIMessage(content="Success")
        mock_agent_executor.invoke.side_effect = [
            ResourceExhausted("Rate limited"),
            {"messages": [mock_response_message]},
        ]

        mock_parsing_llm = Mock(spec=BaseChatModel)
        agent = CodeBoardingAgent(
            repo_dir=self.repo_dir,
            static_analysis=self.mock_analysis,
            system_message="Test",
            agent_llm=self.mock_llm,
            parsing_llm=mock_parsing_llm,
        )

        result = agent._invoke("Test prompt")

        self.assertEqual(result, "Success")
        # Should have retried
        self.assertEqual(mock_agent_executor.invoke.call_count, 2)
        mock_sleep.assert_called_with(30)

    @patch("agents.agent.create_agent")
    @patch("time.sleep")
    def test_invoke_max_retries(self, mock_sleep, mock_create_agent):
        # Test max retries reached
        mock_agent_executor = Mock()
        mock_create_agent.return_value = mock_agent_executor

        # Always raise exception
        mock_agent_executor.invoke.side_effect = Exception("Always fails")

        mock_parsing_llm = Mock(spec=BaseChatModel)
        agent = CodeBoardingAgent(
            repo_dir=self.repo_dir,
            static_analysis=self.mock_analysis,
            system_message="Test",
            agent_llm=self.mock_llm,
            parsing_llm=mock_parsing_llm,
        )

        result = agent._invoke("Test prompt")

        # Should return error message after max retries
        self.assertIn("Could not get response", result)
        self.assertEqual(mock_agent_executor.invoke.call_count, 5)

    @patch("agents.agent.create_agent")
    def test_invoke_with_callbacks(self, mock_create_agent):
        # Test invocation with callbacks
        mock_agent_executor = Mock()
        mock_create_agent.return_value = mock_agent_executor

        mock_response_message = AIMessage(content="Test response")
        mock_agent_executor.invoke.return_value = {"messages": [mock_response_message]}

        mock_parsing_llm = Mock(spec=BaseChatModel)
        agent = CodeBoardingAgent(
            repo_dir=self.repo_dir,
            static_analysis=self.mock_analysis,
            system_message="Test",
            agent_llm=self.mock_llm,
            parsing_llm=mock_parsing_llm,
        )

        result = agent._invoke("Test prompt")

        # Callbacks should be passed to agent
        call_args = mock_agent_executor.invoke.call_args
        config = call_args[1]["config"]
        self.assertIn("callbacks", config)
        # Monitoring callbacks plus per-call cancellation callback.
        self.assertEqual(len(config["callbacks"]), 3)
        self.assertIn(agent.agent_monitoring_callback, config["callbacks"])
        cancellation_callback = next(c for c in config["callbacks"] if isinstance(c, _CancellationCallback))
        self.assertFalse(cancellation_callback.cancellation_event.is_set())

    def test_cancellation_callback_raises_only_after_event_set(self):
        cancellation_event = threading.Event()
        callback = _CancellationCallback(cancellation_event)

        callback.on_llm_start({}, ["prompt"])
        callback.on_chat_model_start({}, [[AIMessage(content="message")]])
        callback.on_tool_start({"name": "tool"}, "input")

        cancellation_event.set()

        with self.assertRaises(_AgentInvocationCancelled):
            callback.on_llm_start({}, ["prompt"])
        with self.assertRaises(_AgentInvocationCancelled):
            callback.on_chat_model_start({}, [[AIMessage(content="message")]])
        with self.assertRaises(_AgentInvocationCancelled):
            callback.on_tool_start({"name": "tool"}, "input")

    @patch("agents.agent.create_agent")
    def test_invoke_with_timeout_sets_cancellation_event_and_stops_worker(self, mock_create_agent):
        mock_agent_executor = Mock()
        started = threading.Event()
        stopped = threading.Event()
        callbacks_seen = []
        step_count = 0

        def invoke(_payload, config):
            nonlocal step_count
            callbacks_seen.extend(config["callbacks"])
            cancellation_callback = next(c for c in config["callbacks"] if isinstance(c, _CancellationCallback))
            started.set()
            try:
                while True:
                    step_count += 1
                    cancellation_callback.on_tool_start({"name": "fake_tool"}, "input")
                    time.sleep(0.005)
            finally:
                stopped.set()

        mock_agent_executor.invoke.side_effect = invoke
        agent = self._create_agent_with_executor(mock_create_agent, mock_agent_executor)

        with self.assertLogs("agents.agent", level="INFO") as logs:
            with self.assertRaisesRegex(TimeoutError, "Agent invocation exceeded 0.05s timeout"):
                agent._invoke_with_timeout(timeout_seconds=0.05, callback_list=[], prompt="Test prompt")
            self.assertTrue(started.wait(1))
            self.assertTrue(stopped.wait(1))

        cancellation_callback = next(c for c in callbacks_seen if isinstance(c, _CancellationCallback))
        self.assertTrue(cancellation_callback.cancellation_event.is_set())
        self.assertGreater(step_count, 0)
        stopped_at = step_count
        time.sleep(0.02)
        self.assertEqual(step_count, stopped_at)
        self.assertTrue(any("cancelled after caller timeout" in message for message in logs.output))

    @patch("agents.agent.create_agent")
    def test_invoke_with_timeout_raises_still_running_when_provider_call_blocks(self, mock_create_agent):
        mock_agent_executor = Mock()
        started = threading.Event()
        release = threading.Event()
        invoke_returned = threading.Event()
        callbacks_seen = []

        def invoke(_payload, config):
            callbacks_seen.extend(config["callbacks"])
            started.set()
            release.wait(1)
            invoke_returned.set()
            return {"messages": [AIMessage(content="late")]}

        mock_agent_executor.invoke.side_effect = invoke
        agent = self._create_agent_with_executor(mock_create_agent, mock_agent_executor)

        with patch("agents.agent._TIMED_OUT_THREAD_JOIN_TIMEOUT_SECONDS", 0.01):
            with self.assertLogs("agents.agent", level="INFO") as logs:
                try:
                    with self.assertRaisesRegex(
                        _AgentInvocationStillRunning, "Agent invocation exceeded 0.05s timeout"
                    ):
                        agent._invoke_with_timeout(timeout_seconds=0.05, callback_list=[], prompt="Test prompt")
                finally:
                    release.set()
                self.assertTrue(invoke_returned.wait(1))
                time.sleep(0.02)

        self.assertTrue(started.is_set())
        cancellation_callback = next(c for c in callbacks_seen if isinstance(c, _CancellationCallback))
        self.assertTrue(cancellation_callback.cancellation_event.is_set())
        self.assertEqual(mock_agent_executor.invoke.call_count, 1)
        self.assertTrue(
            any("Discarding agent invoke response after caller timeout" in message for message in logs.output)
        )

    @patch("agents.agent.create_agent")
    def test_invoke_does_not_retry_when_timed_out_worker_remains_blocked(self, mock_create_agent):
        mock_agent_executor = Mock()
        started = threading.Event()
        release = threading.Event()
        invoke_returned = threading.Event()

        def invoke(_payload, config):
            started.set()
            release.wait(1)
            invoke_returned.set()
            return {"messages": [AIMessage(content="late")]}

        mock_agent_executor.invoke.side_effect = invoke
        agent = self._create_agent_with_executor(mock_create_agent, mock_agent_executor)

        try:
            with (
                patch("agents.agent._AGENT_INVOKE_INITIAL_TIMEOUT_SECONDS", 0.05),
                patch("agents.agent._TIMED_OUT_THREAD_JOIN_TIMEOUT_SECONDS", 0.01),
                patch("agents.retry.time.sleep") as mock_sleep,
            ):
                with self.assertRaisesRegex(TimeoutError, "Agent invocation exceeded 0.05s timeout"):
                    agent._invoke("Test prompt")

            self.assertTrue(started.is_set())
            self.assertEqual(mock_agent_executor.invoke.call_count, 1)
            mock_sleep.assert_not_called()
        finally:
            release.set()
            self.assertTrue(invoke_returned.wait(1))

    @patch("agents.agent.create_agent")
    def test_invoke_with_timeout_returns_fast_response_unchanged(self, mock_create_agent):
        mock_agent_executor = Mock()
        response = {"messages": [AIMessage(content="fast")]}
        captured_config = {}

        def invoke(_payload, config):
            captured_config.update(config)
            return response

        mock_agent_executor.invoke.side_effect = invoke
        agent = self._create_agent_with_executor(mock_create_agent, mock_agent_executor)
        callback_marker = Mock()

        result = agent._invoke_with_timeout(timeout_seconds=1, callback_list=[callback_marker], prompt="Test prompt")

        self.assertIs(result, response)
        callbacks = captured_config["callbacks"]
        self.assertIn(callback_marker, callbacks)
        cancellation_callback = next(c for c in callbacks if isinstance(c, _CancellationCallback))
        self.assertFalse(cancellation_callback.cancellation_event.is_set())

    @patch("agents.agent.create_agent")
    def test_invoke_with_timeout_surfaces_original_exception(self, mock_create_agent):
        mock_agent_executor = Mock()
        error = ValueError("boom")
        mock_agent_executor.invoke.side_effect = error
        agent = self._create_agent_with_executor(mock_create_agent, mock_agent_executor)

        with self.assertRaises(ValueError) as raised:
            agent._invoke_with_timeout(timeout_seconds=1, callback_list=[], prompt="Test prompt")

        self.assertIs(raised.exception, error)

    @patch("agents.agent.create_extractor")
    @patch("agents.agent.create_agent")
    def test_parse_invoke(self, mock_create_agent, mock_create_extractor):
        mock_agent_executor = Mock()
        mock_create_agent.return_value = mock_agent_executor

        mock_response_message = AIMessage(content='{"value": "test_value"}')
        mock_agent_executor.invoke.return_value = {"messages": [mock_response_message]}

        mock_extractor = Mock()
        mock_extractor.invoke.return_value = {"responses": [{"value": "test_value"}]}
        mock_create_extractor.return_value = mock_extractor

        mock_parsing_llm = Mock(spec=BaseChatModel)
        agent = CodeBoardingAgent(
            repo_dir=self.repo_dir,
            static_analysis=self.mock_analysis,
            system_message="Test",
            agent_llm=self.mock_llm,
            parsing_llm=mock_parsing_llm,
        )

        result = agent._parse_invoke("Test prompt", TestResponse)

        self.assertIsInstance(result, TestResponse)
        self.assertEqual(result.value, "test_value")

    @patch("agents.agent.create_agent")
    def test_get_monitoring_results_no_callback(self, mock_create_agent):
        # Test getting monitoring results when no callback exists
        mock_create_agent.return_value = Mock()

        mock_parsing_llm = Mock(spec=BaseChatModel)
        agent = CodeBoardingAgent(
            repo_dir=self.repo_dir,
            static_analysis=self.mock_analysis,
            system_message="Test",
            agent_llm=self.mock_llm,
            parsing_llm=mock_parsing_llm,
        )

        results = agent.get_monitoring_results()

        # Should return stats structure with zeros
        self.assertIn("token_usage", results)
        self.assertEqual(results["token_usage"]["total_tokens"], 0)
        self.assertEqual(results["token_usage"]["input_tokens"], 0)
        self.assertEqual(results["token_usage"]["output_tokens"], 0)

    @patch("agents.agent.create_agent")
    def test_get_monitoring_results_with_callback(self, mock_create_agent):
        # Test getting monitoring results with callback
        mock_create_agent.return_value = Mock()

        mock_parsing_llm = Mock(spec=BaseChatModel)
        agent = CodeBoardingAgent(
            repo_dir=self.repo_dir,
            static_analysis=self.mock_analysis,
            system_message="Test",
            agent_llm=self.mock_llm,
            parsing_llm=mock_parsing_llm,
        )

        # Manually set stats on the agent's stats container
        agent.agent_stats.input_tokens = 100
        agent.agent_stats.output_tokens = 50
        agent.agent_stats.total_tokens = 150
        agent.agent_stats.tool_counts["tool1"] = 5
        agent.agent_stats.tool_errors["tool1"] = 1
        agent.agent_stats.tool_latency_ms["tool1"] = [100, 200, 150]

        results = agent.get_monitoring_results()

        # Should return monitoring stats
        self.assertIn("token_usage", results)
        self.assertEqual(results["token_usage"]["input_tokens"], 100)
        self.assertEqual(results["token_usage"]["output_tokens"], 50)
        self.assertIn("tool_usage", results)
        self.assertEqual(results["tool_usage"]["counts"]["tool1"], 5)

    @patch("agents.agent.create_extractor")
    @patch("agents.agent.create_agent")
    def test_parse_response_fenced_json(self, mock_create_agent, mock_create_extractor):
        mock_create_agent.return_value = Mock()

        mock_extractor = Mock()
        mock_extractor.invoke.return_value = {"responses": [{"value": "success"}]}
        mock_create_extractor.return_value = mock_extractor

        mock_parsing_llm = Mock(spec=BaseChatModel)
        agent = CodeBoardingAgent(
            repo_dir=self.repo_dir,
            static_analysis=self.mock_analysis,
            system_message="Test",
            agent_llm=self.mock_llm,
            parsing_llm=mock_parsing_llm,
        )

        fenced = '```json\n{"value": "success"}\n```'
        result = agent._parse_response("Test prompt", fenced, TestResponse)

        self.assertIsInstance(result, TestResponse)
        self.assertEqual(result.value, "success")

    @patch("agents.agent.create_extractor")
    @patch("agents.agent.create_agent")
    def test_parse_response_cluster_analysis_bug_shape(self, mock_create_agent, mock_create_extractor):
        from agents.agent_responses import ClusterAnalysis

        mock_create_agent.return_value = Mock()

        mock_extractor = Mock()
        mock_extractor.invoke.return_value = {
            "responses": [
                {
                    "cluster_components": [
                        {
                            "name": "VS Code Extension Host & View Providers",
                            "cluster_ids": [1, 15],
                            "description": "Hosts the extension and view providers.",
                            "interactions": "talks to webview",
                        },
                        {"name": "Analysis Pipeline", "cluster_ids": [2, 3], "description": "Runs analysis."},
                    ]
                }
            ]
        }
        mock_create_extractor.return_value = mock_extractor

        mock_parsing_llm = Mock(spec=BaseChatModel)
        agent = CodeBoardingAgent(
            repo_dir=self.repo_dir,
            static_analysis=self.mock_analysis,
            system_message="Test",
            agent_llm=self.mock_llm,
            parsing_llm=mock_parsing_llm,
        )

        fenced = (
            "```json\n"
            "{\n"
            '  "cluster_components": [\n'
            "    {\n"
            '      "name": "VS Code Extension Host & View Providers",\n'
            '      "cluster_ids": [1, 15],\n'
            '      "description": "Hosts the extension and view providers.",\n'
            '      "interactions": "talks to webview"\n'
            "    },\n"
            "    {\n"
            '      "name": "Analysis Pipeline",\n'
            '      "cluster_ids": [2, 3],\n'
            '      "description": "Runs analysis."\n'
            "    }\n"
            "  ]\n"
            "}\n"
            "```"
        )

        result = agent._parse_response("Test prompt", fenced, ClusterAnalysis)

        self.assertIsInstance(result, ClusterAnalysis)
        self.assertEqual(len(result.cluster_components), 2)
        self.assertEqual(result.cluster_components[0].name, "VS Code Extension Host & View Providers")
        self.assertEqual(result.cluster_components[0].cluster_ids, [1, 15])
        self.assertEqual(result.cluster_components[1].cluster_ids, [2, 3])

    @patch("agents.agent.create_extractor")
    @patch("agents.agent.create_agent")
    def test_parse_response_invalid_falls_back_to_llm_repair(self, mock_create_agent, mock_create_extractor):
        mock_create_agent.return_value = Mock()

        mock_extractor = Mock()
        mock_extractor.invoke.return_value = {"responses": [], "messages": []}
        mock_create_extractor.return_value = mock_extractor

        mock_parsing_llm = Mock(spec=BaseChatModel)
        agent = CodeBoardingAgent(
            repo_dir=self.repo_dir,
            static_analysis=self.mock_analysis,
            system_message="Test",
            agent_llm=self.mock_llm,
            parsing_llm=mock_parsing_llm,
        )

        with patch.object(agent, "_structured_parse", return_value=TestResponse(value="repaired")) as repair:
            result = agent._parse_response("Test prompt", "not json at all", TestResponse)

        self.assertEqual(result.value, "repaired")
        repair.assert_called_once()

    @patch("agents.agent.create_extractor")
    @patch("agents.agent.create_agent")
    def test_parse_response_empty_raises(self, mock_create_agent, mock_create_extractor):
        mock_create_agent.return_value = Mock()

        mock_extractor = Mock()
        mock_extractor.invoke.side_effect = ValueError("empty")
        mock_create_extractor.return_value = mock_extractor

        mock_parsing_llm = Mock(spec=BaseChatModel)
        agent = CodeBoardingAgent(
            repo_dir=self.repo_dir,
            static_analysis=self.mock_analysis,
            system_message="Test",
            agent_llm=self.mock_llm,
            parsing_llm=mock_parsing_llm,
        )

        with self.assertRaises(Exception):
            agent._parse_response("Test prompt", "", TestResponse)

    @patch("agents.agent.create_agent")
    def test_tools_initialized(self, mock_create_agent):
        # Test that all required tools are initialized
        mock_create_agent.return_value = Mock()

        mock_parsing_llm = Mock(spec=BaseChatModel)
        agent = CodeBoardingAgent(
            repo_dir=self.repo_dir,
            static_analysis=self.mock_analysis,
            system_message="Test",
            agent_llm=self.mock_llm,
            parsing_llm=mock_parsing_llm,
        )

        # Check tools are initialized
        self.assertIsNotNone(agent.read_source_reference)
        self.assertIsNotNone(agent.read_packages_tool)
        self.assertIsNotNone(agent.read_structure_tool)
        self.assertIsNotNone(agent.read_file_structure)
        self.assertIsNotNone(agent.read_cfg_tool)
        self.assertIsNotNone(agent.read_method_invocations_tool)
        self.assertIsNotNone(agent.read_file_tool)
        self.assertIsNotNone(agent.read_docs)
        self.assertIsNotNone(agent.external_deps_tool)

    @patch("agents.agent.create_agent")
    @patch("time.sleep")
    def test_invoke_raises_immediately_on_404(self, mock_sleep, mock_create_agent):
        """HTTP 404 (e.g. retired model) should raise immediately without retrying."""
        mock_agent_executor = Mock()
        mock_create_agent.return_value = mock_agent_executor

        # Simulate a NotFoundError-like exception with status_code=404
        error = Exception("model not found")
        error.status_code = 404  # type: ignore[attr-defined]
        mock_agent_executor.invoke.side_effect = error

        mock_parsing_llm = Mock(spec=BaseChatModel)
        agent = CodeBoardingAgent(
            repo_dir=self.repo_dir,
            static_analysis=self.mock_analysis,
            system_message="Test",
            agent_llm=self.mock_llm,
            parsing_llm=mock_parsing_llm,
        )

        with self.assertRaises(Exception, msg="model not found"):
            agent._invoke("Test prompt")

        # Should NOT have retried — only one call
        self.assertEqual(mock_agent_executor.invoke.call_count, 1)
        mock_sleep.assert_not_called()

    @patch("agents.agent.create_agent")
    def test_agent_created_with_tools(self, mock_create_agent):
        # Test that agent is created with correct tools
        mock_create_agent.return_value = Mock()

        mock_parsing_llm = Mock(spec=BaseChatModel)
        agent = CodeBoardingAgent(
            repo_dir=self.repo_dir,
            static_analysis=self.mock_analysis,
            system_message="Test",
            agent_llm=self.mock_llm,
            parsing_llm=mock_parsing_llm,
        )

        # Verify create_agent was called with tools
        call_args = mock_create_agent.call_args
        self.assertIn("tools", call_args[1])
        tools = call_args[1]["tools"]
        # Should have at least 5 tools
        self.assertGreaterEqual(len(tools), 5)


class TestIncludeHidden(unittest.TestCase):
    def test_extractor_str_hides_hidden_fields_by_default(self):
        from agents.agent_responses import ClustersComponent

        prompt = ClustersComponent.extractor_str()
        for field in ("existing_component_id", "parent_id", "redetail_needed"):
            self.assertNotIn(field, prompt)

    def test_extractor_str_shows_hidden_fields_when_requested(self):
        from agents.agent_responses import ClustersComponent

        prompt = ClustersComponent.extractor_str(include_hidden=True)
        for field in ("existing_component_id", "parent_id", "redetail_needed"):
            self.assertIn(field, prompt)

    def test_model_json_schema_hides_hidden_fields_by_default(self):
        from agents.agent_responses import ClustersComponent

        schema = ClustersComponent.model_json_schema()
        props = schema.get("properties", {})
        for field in ("existing_component_id", "parent_id", "redetail_needed"):
            self.assertNotIn(field, props)
        defs = schema.get("$defs", {}).get("ClustersComponent", {}).get("properties", {})
        for field in ("existing_component_id", "parent_id", "redetail_needed"):
            self.assertNotIn(field, defs)

    def test_model_json_schema_shows_hidden_fields_when_requested(self):
        from agents.agent_responses import ClustersComponent

        schema = ClustersComponent.model_json_schema(include_hidden=True)
        props = schema.get("properties", {})
        for field in ("existing_component_id", "parent_id", "redetail_needed"):
            self.assertIn(field, props)

    def test_parse_response_uses_hidden_schema_for_structured_parse(self):
        from agents.agent_responses import ClusterAnalysis

        mock_create_agent = Mock(return_value=Mock())
        with patch("agents.agent.create_agent", mock_create_agent):
            mock_parsing_llm = Mock(spec=BaseChatModel)
            agent = CodeBoardingAgent(
                repo_dir=Path(tempfile.mkdtemp()),
                static_analysis=Mock(spec=StaticAnalysisResults),
                system_message="Test",
                agent_llm=Mock(),
                parsing_llm=mock_parsing_llm,
            )

        captured_instructions = {}

        original_structured_parse = agent._structured_parse

        def spy_structured_parse(message_content, parser, format_instructions=None):
            captured_instructions["value"] = format_instructions or parser.get_format_instructions()
            raise RuntimeError("stop here")

        agent._structured_parse = spy_structured_parse

        try:
            agent._parse_response("prompt", "response", ClusterAnalysis, include_hidden=True)
        except Exception:
            pass

        instructions = captured_instructions.get("value", "")
        self.assertIn("existing_component_id", instructions)
        self.assertIn("parent_id", instructions)
        self.assertIn("redetail_needed", instructions)

    def test_parse_response_hides_fields_by_default(self):
        from agents.agent_responses import ClusterAnalysis

        mock_create_agent = Mock(return_value=Mock())
        with patch("agents.agent.create_agent", mock_create_agent):
            mock_parsing_llm = Mock(spec=BaseChatModel)
            agent = CodeBoardingAgent(
                repo_dir=Path(tempfile.mkdtemp()),
                static_analysis=Mock(spec=StaticAnalysisResults),
                system_message="Test",
                agent_llm=Mock(),
                parsing_llm=mock_parsing_llm,
            )

        captured_instructions = {}

        def spy_structured_parse(message_content, parser, format_instructions=None):
            captured_instructions["value"] = format_instructions or parser.get_format_instructions()
            raise RuntimeError("stop here")

        agent._structured_parse = spy_structured_parse

        try:
            agent._parse_response("prompt", "response", ClusterAnalysis)
        except Exception:
            pass

        instructions = captured_instructions.get("value", "")
        self.assertNotIn("existing_component_id", instructions)
        self.assertNotIn("redetail_needed", instructions)


if __name__ == "__main__":
    unittest.main()
