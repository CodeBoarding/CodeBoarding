import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, MagicMock, patch

from langchain_core.messages import AIMessage
from langchain_core.language_models import BaseChatModel
from pydantic import ValidationError

from agents.agent import CodeBoardingAgent, LargeModelAgent
from agents.agent_responses import LLMBaseModel
from agents.llm_config import LLM_PROVIDERS
from static_analyzer.analysis_result import StaticAnalysisResults
from monitoring.stats import RunStats, current_stats


class TestResponse(LLMBaseModel):
    """Test response model for parsing tests"""

    value: str

    def llm_str(self):
        return self.value

    @classmethod
    def extractor_str(cls) -> str:
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
        import shutil

        shutil.rmtree(self.temp_dir, ignore_errors=True)
        self.env_patcher.stop()

        # Reset monitoring context
        current_stats.reset(self.token)

    @patch("agents.agent.create_react_agent")
    def test_init_with_openai(self, mock_create_agent):
        # Test initialization with OpenAI
        from agents.llm_config import LLM_PROVIDERS

        mock_llm = Mock()
        mock_agent_executor = Mock()
        mock_create_agent.return_value = mock_agent_executor

        with patch.object(LLM_PROVIDERS["openai"], "chat_class", return_value=mock_llm):
            agent = LargeModelAgent(
                repo_dir=self.repo_dir, static_analysis=self.mock_analysis, system_message="Test system message"
            )

            # Verify agent was created
            mock_create_agent.assert_called_once()
            # Verify attributes
            self.assertEqual(agent.repo_dir, self.repo_dir)
            self.assertEqual(agent.static_analysis, self.mock_analysis)

    @patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test_key"}, clear=True)
    @patch("agents.agent.create_react_agent")
    def test_init_with_anthropic(self, mock_create_agent):
        # Test initialization with Anthropic
        from agents.llm_config import LLM_PROVIDERS

        mock_llm = Mock()
        mock_create_agent.return_value = Mock()

        with patch.object(LLM_PROVIDERS["anthropic"], "chat_class", return_value=mock_llm):
            agent = LargeModelAgent(repo_dir=self.repo_dir, static_analysis=self.mock_analysis, system_message="Test")
            self.assertIsNotNone(agent)

    @patch.dict(os.environ, {"GOOGLE_API_KEY": "test_key"}, clear=True)
    @patch("agents.agent.create_react_agent")
    def test_init_with_google(self, mock_create_agent):
        # Test initialization with Google
        from agents.llm_config import LLM_PROVIDERS

        mock_llm = Mock()
        mock_create_agent.return_value = Mock()

        with patch.object(LLM_PROVIDERS["google"], "chat_class", return_value=mock_llm):
            agent = LargeModelAgent(repo_dir=self.repo_dir, static_analysis=self.mock_analysis, system_message="Test")
            self.assertIsNotNone(agent)

    @patch.dict(os.environ, {"AWS_BEARER_TOKEN_BEDROCK": "test_token"}, clear=True)
    @patch("agents.agent.create_react_agent")
    def test_init_with_aws(self, mock_create_agent):
        # Test initialization with AWS Bedrock
        from agents.llm_config import LLM_PROVIDERS

        mock_llm = Mock()
        mock_create_agent.return_value = Mock()

        with patch.object(LLM_PROVIDERS["aws"], "chat_class", return_value=mock_llm):
            agent = LargeModelAgent(repo_dir=self.repo_dir, static_analysis=self.mock_analysis, system_message="Test")
            self.assertIsNotNone(agent)

    @patch.dict(os.environ, {"CEREBRAS_API_KEY": "test_key"}, clear=True)
    @patch("agents.agent.create_instructor_client_from_env")
    @patch("agents.agent.create_react_agent")
    def test_init_with_cerebras(self, mock_create_agent, mock_create_instructor):
        # Test initialization with Cerebras
        from agents.llm_config import LLM_PROVIDERS

        mock_llm = Mock()
        mock_create_agent.return_value = Mock()
        mock_create_instructor.return_value = (Mock(), "test-model")

        with patch.object(LLM_PROVIDERS["cerebras"], "chat_class", return_value=mock_llm):
            agent = LargeModelAgent(repo_dir=self.repo_dir, static_analysis=self.mock_analysis, system_message="Test")
            self.assertIsNotNone(agent)

    @patch.dict(os.environ, {"OLLAMA_BASE_URL": "http://localhost:11434"}, clear=True)
    @patch("agents.agent.create_react_agent")
    def test_init_with_ollama(self, mock_create_agent):
        # Test initialization with Ollama
        from agents.llm_config import LLM_PROVIDERS

        mock_llm = Mock()
        mock_create_agent.return_value = Mock()

        with patch.object(LLM_PROVIDERS["ollama"], "chat_class", return_value=mock_llm):
            agent = LargeModelAgent(repo_dir=self.repo_dir, static_analysis=self.mock_analysis, system_message="Test")
            self.assertIsNotNone(agent)

    @patch.dict(os.environ, {}, clear=True)
    def test_init_no_api_key(self):
        # Test initialization without any API key
        with self.assertRaises(ValueError) as context:
            LargeModelAgent(repo_dir=self.repo_dir, static_analysis=self.mock_analysis, system_message="Test")

        self.assertIn("No valid LLM configuration found", str(context.exception))

    @patch("agents.agent.create_react_agent")
    def test_invoke_success(self, mock_create_agent):
        # Test successful invocation
        mock_agent_executor = Mock()
        mock_create_agent.return_value = mock_agent_executor

        # Mock agent response
        mock_response_message = AIMessage(content="Test response")
        mock_agent_executor.invoke.return_value = {"messages": [mock_response_message]}

        agent = CodeBoardingAgent(
            repo_dir=self.repo_dir,
            static_analysis=self.mock_analysis,
            system_message="Test",
            llm=self.mock_llm,
            model_name="test-model",
            instructor_client=Mock(),
            instructor_model_name="test-instructor-model",
        )

        result = agent._invoke("Test prompt")

        self.assertEqual(result, "Test response")
        mock_agent_executor.invoke.assert_called_once()

    @patch("agents.agent.create_react_agent")
    def test_invoke_with_list_content(self, mock_create_agent):
        # Test invocation with list content response
        mock_agent_executor = Mock()
        mock_create_agent.return_value = mock_agent_executor

        # Mock agent response with list content
        mock_response_message = AIMessage(content=["Part 1", "Part 2"])
        mock_agent_executor.invoke.return_value = {"messages": [mock_response_message]}

        agent = CodeBoardingAgent(
            repo_dir=self.repo_dir,
            static_analysis=self.mock_analysis,
            system_message="Test",
            llm=self.mock_llm,
            model_name="test-model",
            instructor_client=Mock(),
            instructor_model_name="test-instructor-model",
        )

        result = agent._invoke("Test prompt")

        self.assertEqual(result, "Part 1Part 2")

    @patch("agents.agent.create_react_agent")
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

        agent = CodeBoardingAgent(
            repo_dir=self.repo_dir,
            static_analysis=self.mock_analysis,
            system_message="Test",
            llm=self.mock_llm,
            model_name="test-model",
            instructor_client=Mock(),
            instructor_model_name="test-instructor-model",
        )

        result = agent._invoke("Test prompt")

        self.assertEqual(result, "Success")
        # Should have retried
        self.assertEqual(mock_agent_executor.invoke.call_count, 2)
        mock_sleep.assert_called_with(30)

    @patch("agents.agent.create_react_agent")
    @patch("time.sleep")
    def test_invoke_max_retries(self, mock_sleep, mock_create_agent):
        # Test max retries reached
        mock_agent_executor = Mock()
        mock_create_agent.return_value = mock_agent_executor

        # Always raise exception
        mock_agent_executor.invoke.side_effect = Exception("Always fails")

        agent = CodeBoardingAgent(
            repo_dir=self.repo_dir,
            static_analysis=self.mock_analysis,
            system_message="Test",
            llm=self.mock_llm,
            model_name="test-model",
            instructor_client=Mock(),
            instructor_model_name="test-instructor-model",
        )

        result = agent._invoke("Test prompt")

        # Should return error message after max retries
        self.assertIn("Could not get response", result)
        self.assertEqual(mock_agent_executor.invoke.call_count, 5)

    @patch("agents.agent.create_react_agent")
    def test_invoke_with_callbacks(self, mock_create_agent):
        # Test invocation with callbacks
        mock_agent_executor = Mock()
        mock_create_agent.return_value = mock_agent_executor

        mock_response_message = AIMessage(content="Test response")
        mock_agent_executor.invoke.return_value = {"messages": [mock_response_message]}

        agent = CodeBoardingAgent(
            repo_dir=self.repo_dir,
            static_analysis=self.mock_analysis,
            system_message="Test",
            llm=self.mock_llm,
            model_name="test-model",
            instructor_client=Mock(),
            instructor_model_name="test-instructor-model",
        )

        result = agent._invoke("Test prompt")

        # Callbacks should be passed to agent
        call_args = mock_agent_executor.invoke.call_args
        config = call_args[1]["config"]
        self.assertIn("callbacks", config)
        # Should have 2 callbacks: module-level MONITORING_CALLBACK and agent_monitoring_callback
        self.assertEqual(len(config["callbacks"]), 2)
        self.assertIn(agent.agent_monitoring_callback, config["callbacks"])

    @patch("agents.agent.create_react_agent")
    def test_parse_invoke(self, mock_create_agent):
        # Test parse_invoke method
        mock_agent_executor = Mock()
        mock_create_agent.return_value = mock_agent_executor

        # Mock response
        mock_response_message = AIMessage(content='{"value": "test_value"}')
        mock_agent_executor.invoke.return_value = {"messages": [mock_response_message]}

        # Mock instructor client
        mock_instructor_client = Mock()
        mock_instructor_client.chat.completions.create.return_value = TestResponse(value="test_value")

        agent = CodeBoardingAgent(
            repo_dir=self.repo_dir,
            static_analysis=self.mock_analysis,
            system_message="Test",
            llm=self.mock_llm,
            model_name="test-model",
            instructor_client=mock_instructor_client,
            instructor_model_name="test-instructor-model",
        )

        result = agent._parse_invoke("Test prompt", TestResponse)

        # Should return parsed response
        self.assertIsInstance(result, TestResponse)
        self.assertEqual(result.value, "test_value")

    @patch("agents.agent.create_react_agent")
    def test_get_monitoring_results_no_callback(self, mock_create_agent):
        # Test getting monitoring results when no callback exists
        mock_create_agent.return_value = Mock()

        agent = CodeBoardingAgent(
            repo_dir=self.repo_dir,
            static_analysis=self.mock_analysis,
            system_message="Test",
            llm=self.mock_llm,
            model_name="test-model",
            instructor_client=Mock(),
            instructor_model_name="test-instructor-model",
        )

        results = agent.get_monitoring_results()

        # Should return stats structure with zeros
        self.assertIn("token_usage", results)
        self.assertEqual(results["token_usage"]["total_tokens"], 0)
        self.assertEqual(results["token_usage"]["input_tokens"], 0)
        self.assertEqual(results["token_usage"]["output_tokens"], 0)

    @patch("agents.agent.create_react_agent")
    def test_get_monitoring_results_with_callback(self, mock_create_agent):
        # Test getting monitoring results with callback
        mock_create_agent.return_value = Mock()

        agent = CodeBoardingAgent(
            repo_dir=self.repo_dir,
            static_analysis=self.mock_analysis,
            system_message="Test",
            llm=self.mock_llm,
            model_name="test-model",
            instructor_client=Mock(),
            instructor_model_name="test-instructor-model",
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

    @patch("agents.agent.create_react_agent")
    def test_initialize_llm_custom_model(self, mock_create_agent):
        # Test LLM initialization with custom model
        from agents.llm_config import LLM_PROVIDERS

        mock_llm = Mock()
        mock_create_agent.return_value = Mock()
        mock_chat_openai = Mock(return_value=mock_llm)

        with patch.object(LLM_PROVIDERS["openai"], "chat_class", mock_chat_openai):
            with patch.dict(os.environ, {"OPENAI_API_KEY": "test_key", "AGENT_MODEL": "gpt-4-turbo"}, clear=True):
                agent = LargeModelAgent(
                    repo_dir=self.repo_dir, static_analysis=self.mock_analysis, system_message="Test"
                )

                # Check that custom model was used
                call_args = mock_chat_openai.call_args
                self.assertEqual(call_args[1]["model"], "gpt-4-turbo")

    @patch("agents.agent.create_react_agent")
    @patch("time.sleep")
    def test_parse_response_with_retry(self, mock_sleep, mock_create_agent):
        # Test parse_response with retry logic
        mock_create_agent.return_value = Mock()

        # Mock instructor client to fail first with ValidationError, then succeed
        mock_instructor_client = Mock()
        mock_instructor_client.chat.completions.create.side_effect = [
            ValidationError.from_exception_data("First attempt fails", []),
            TestResponse(value="success"),
        ]

        agent = CodeBoardingAgent(
            repo_dir=self.repo_dir,
            static_analysis=self.mock_analysis,
            system_message="Test",
            llm=self.mock_llm,
            model_name="test-model",
            instructor_client=mock_instructor_client,
            instructor_model_name="test-instructor-model",
        )

        result = agent._parse_response("Test prompt", '{"value": "success"}', TestResponse, max_retries=5)

        # Should succeed after retry
        self.assertIsInstance(result, TestResponse)
        self.assertEqual(result.value, "success")

    @patch("agents.agent.create_react_agent")
    def test_tools_initialized(self, mock_create_agent):
        # Test that all required tools are initialized
        mock_create_agent.return_value = Mock()

        agent = CodeBoardingAgent(
            repo_dir=self.repo_dir,
            static_analysis=self.mock_analysis,
            system_message="Test",
            llm=self.mock_llm,
            model_name="test-model",
            instructor_client=Mock(),
            instructor_model_name="test-instructor-model",
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

    @patch("agents.agent.create_react_agent")
    def test_agent_created_with_tools(self, mock_create_agent):
        # Test that agent is created with correct tools
        mock_create_agent.return_value = Mock()

        agent = CodeBoardingAgent(
            repo_dir=self.repo_dir,
            static_analysis=self.mock_analysis,
            system_message="Test",
            llm=self.mock_llm,
            model_name="test-model",
            instructor_client=Mock(),
            instructor_model_name="test-instructor-model",
        )

        # Verify create_react_agent was called with tools
        call_args = mock_create_agent.call_args
        self.assertIn("tools", call_args[1])
        tools = call_args[1]["tools"]
        # Should have at least 5 tools
        self.assertGreaterEqual(len(tools), 5)


if __name__ == "__main__":
    unittest.main()
