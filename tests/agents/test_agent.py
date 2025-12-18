import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, MagicMock, patch

from langchain_core.messages import AIMessage
from pydantic import BaseModel

from agents.agent import CodeBoardingAgent
from static_analyzer.analysis_result import StaticAnalysisResults
from monitoring.stats import RunStats, current_stats


class TestResponse(BaseModel):
    """Test response model for parsing tests"""

    value: str

    @staticmethod
    def extractor_str():
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
        self.env_patcher = patch.dict(
            os.environ, {"OPENAI_API_KEY": "test_key", "CODEBOARDING_MODEL": "gpt-4o"}, clear=True
        )
        self.env_patcher.start()

        # Set up monitoring context
        self.run_stats = RunStats()
        self.token = current_stats.set(self.run_stats)

    def tearDown(self):
        # Clean up
        import shutil

        shutil.rmtree(self.temp_dir, ignore_errors=True)
        self.env_patcher.stop()

        # Reset monitoring context
        current_stats.reset(self.token)

    @patch("agents.agent.ChatOpenAI")
    @patch("agents.agent.create_react_agent")
    def test_init_with_openai(self, mock_create_agent, mock_chat_openai):
        # Test initialization with OpenAI
        mock_llm = Mock()
        mock_chat_openai.return_value = mock_llm
        mock_agent_executor = Mock()
        mock_create_agent.return_value = mock_agent_executor

        agent = CodeBoardingAgent(
            repo_dir=self.repo_dir, static_analysis=self.mock_analysis, system_message="Test system message"
        )

        # Verify LLM was initialized twice (once for llm, once for extractor_llm)
        self.assertEqual(mock_chat_openai.call_count, 2)
        # Verify agent was created
        mock_create_agent.assert_called_once()
        # Verify attributes
        self.assertEqual(agent.repo_dir, self.repo_dir)
        self.assertEqual(agent.static_analysis, self.mock_analysis)

    @patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test_key"}, clear=True)
    @patch("agents.agent.ChatAnthropic")
    @patch("agents.agent.create_react_agent")
    def test_init_with_anthropic(self, mock_create_agent, mock_chat_anthropic):
        # Test initialization with Anthropic
        mock_llm = Mock()
        mock_chat_anthropic.return_value = mock_llm
        mock_create_agent.return_value = Mock()

        agent = CodeBoardingAgent(repo_dir=self.repo_dir, static_analysis=self.mock_analysis, system_message="Test")

        # Verify Anthropic LLM was initialized twice (once for llm, once for extractor_llm)
        self.assertEqual(mock_chat_anthropic.call_count, 2)

    @patch.dict(os.environ, {"GOOGLE_API_KEY": "test_key"}, clear=True)
    @patch("agents.agent.ChatGoogleGenerativeAI")
    @patch("agents.agent.create_react_agent")
    def test_init_with_google(self, mock_create_agent, mock_chat_google):
        # Test initialization with Google
        mock_llm = Mock()
        mock_chat_google.return_value = mock_llm
        mock_create_agent.return_value = Mock()

        agent = CodeBoardingAgent(repo_dir=self.repo_dir, static_analysis=self.mock_analysis, system_message="Test")

        # Verify Google LLM was initialized twice (once for llm, once for extractor_llm)
        self.assertEqual(mock_chat_google.call_count, 2)

    @patch.dict(os.environ, {"AWS_BEARER_TOKEN_BEDROCK": "test_token"}, clear=True)
    @patch("agents.agent.load_dotenv")
    @patch("agents.agent.ChatBedrockConverse")
    @patch("agents.agent.create_react_agent")
    def test_init_with_aws(self, mock_create_agent, mock_chat_bedrock, mock_load_dotenv):
        # Test initialization with AWS Bedrock
        mock_llm = Mock()
        mock_chat_bedrock.return_value = mock_llm
        mock_create_agent.return_value = Mock()

        agent = CodeBoardingAgent(repo_dir=self.repo_dir, static_analysis=self.mock_analysis, system_message="Test")

        # Verify Bedrock LLM was initialized twice (once for llm, once for extractor_llm)
        self.assertEqual(mock_chat_bedrock.call_count, 2)

    @patch.dict(os.environ, {"CEREBRAS_API_KEY": "test_key"}, clear=True)
    @patch("agents.agent.load_dotenv")
    @patch("agents.agent.ChatCerebras")
    @patch("agents.agent.create_react_agent")
    def test_init_with_cerebras(self, mock_create_agent, mock_chat_cerebras, mock_load_dotenv):
        # Test initialization with Cerebras
        mock_llm = Mock()
        mock_chat_cerebras.return_value = mock_llm
        mock_create_agent.return_value = Mock()

        agent = CodeBoardingAgent(repo_dir=self.repo_dir, static_analysis=self.mock_analysis, system_message="Test")

        # Verify Cerebras LLM was initialized twice (once for llm, once for extractor_llm)
        self.assertEqual(mock_chat_cerebras.call_count, 2)

    @patch.dict(os.environ, {"OLLAMA_BASE_URL": "http://localhost:11434"}, clear=True)
    @patch("agents.agent.load_dotenv")
    @patch("agents.agent.ChatOllama")
    @patch("agents.agent.create_react_agent")
    def test_init_with_ollama(self, mock_create_agent, mock_chat_ollama, mock_load_dotenv):
        # Test initialization with Ollama
        mock_llm = Mock()
        mock_chat_ollama.return_value = mock_llm
        mock_create_agent.return_value = Mock()

        agent = CodeBoardingAgent(repo_dir=self.repo_dir, static_analysis=self.mock_analysis, system_message="Test")

        # Verify Ollama LLM was initialized twice (once for llm, once for extractor_llm)
        self.assertEqual(mock_chat_ollama.call_count, 2)

    @patch.dict(os.environ, {}, clear=True)
    @patch("agents.agent.load_dotenv")
    def test_init_no_api_key(self, mock_load_dotenv):
        # Test initialization without any API key
        with self.assertRaises(ValueError) as context:
            CodeBoardingAgent(repo_dir=self.repo_dir, static_analysis=self.mock_analysis, system_message="Test")

        self.assertIn("No valid API key", str(context.exception))

    @patch("agents.agent.ChatOpenAI")
    @patch("agents.agent.create_react_agent")
    def test_invoke_success(self, mock_create_agent, mock_chat_openai):
        # Test successful invocation
        mock_llm = Mock()
        mock_chat_openai.return_value = mock_llm

        mock_agent_executor = Mock()
        mock_create_agent.return_value = mock_agent_executor

        # Mock agent response
        mock_response_message = AIMessage(content="Test response")
        mock_agent_executor.invoke.return_value = {"messages": [mock_response_message]}

        agent = CodeBoardingAgent(repo_dir=self.repo_dir, static_analysis=self.mock_analysis, system_message="Test")

        result = agent._invoke("Test prompt")

        self.assertEqual(result, "Test response")
        mock_agent_executor.invoke.assert_called_once()

    @patch("agents.agent.ChatOpenAI")
    @patch("agents.agent.create_react_agent")
    def test_invoke_with_list_content(self, mock_create_agent, mock_chat_openai):
        # Test invocation with list content response
        mock_llm = Mock()
        mock_chat_openai.return_value = mock_llm

        mock_agent_executor = Mock()
        mock_create_agent.return_value = mock_agent_executor

        # Mock agent response with list content
        mock_response_message = AIMessage(content=["Part 1", "Part 2"])
        mock_agent_executor.invoke.return_value = {"messages": [mock_response_message]}

        agent = CodeBoardingAgent(repo_dir=self.repo_dir, static_analysis=self.mock_analysis, system_message="Test")

        result = agent._invoke("Test prompt")

        self.assertEqual(result, "Part 1Part 2")

    @patch("agents.agent.ChatOpenAI")
    @patch("agents.agent.create_react_agent")
    @patch("time.sleep")
    def test_invoke_with_retry(self, mock_sleep, mock_create_agent, mock_chat_openai):
        # Test invocation with retry on ResourceExhausted
        from google.api_core.exceptions import ResourceExhausted

        mock_llm = Mock()
        mock_chat_openai.return_value = mock_llm

        mock_agent_executor = Mock()
        mock_create_agent.return_value = mock_agent_executor

        # First call raises exception, second succeeds
        mock_response_message = AIMessage(content="Success")
        mock_agent_executor.invoke.side_effect = [
            ResourceExhausted("Rate limited"),
            {"messages": [mock_response_message]},
        ]

        agent = CodeBoardingAgent(repo_dir=self.repo_dir, static_analysis=self.mock_analysis, system_message="Test")

        result = agent._invoke("Test prompt")

        self.assertEqual(result, "Success")
        # Should have retried
        self.assertEqual(mock_agent_executor.invoke.call_count, 2)
        mock_sleep.assert_called_with(60)

    @patch("agents.agent.ChatOpenAI")
    @patch("agents.agent.create_react_agent")
    @patch("time.sleep")
    def test_invoke_max_retries(self, mock_sleep, mock_create_agent, mock_chat_openai):
        # Test max retries reached
        mock_llm = Mock()
        mock_chat_openai.return_value = mock_llm

        mock_agent_executor = Mock()
        mock_create_agent.return_value = mock_agent_executor

        # Always raise exception
        mock_agent_executor.invoke.side_effect = Exception("Always fails")

        agent = CodeBoardingAgent(repo_dir=self.repo_dir, static_analysis=self.mock_analysis, system_message="Test")

        result = agent._invoke("Test prompt")

        # Should return error message after max retries
        self.assertIn("Could not get response", result)
        self.assertEqual(mock_agent_executor.invoke.call_count, 5)

    @patch("agents.agent.ChatOpenAI")
    @patch("agents.agent.create_react_agent")
    def test_invoke_with_callbacks(self, mock_create_agent, mock_chat_openai):
        # Test invocation with callbacks
        mock_llm = Mock()
        mock_chat_openai.return_value = mock_llm

        mock_agent_executor = Mock()
        mock_create_agent.return_value = mock_agent_executor

        mock_response_message = AIMessage(content="Test response")
        mock_agent_executor.invoke.return_value = {"messages": [mock_response_message]}

        agent = CodeBoardingAgent(repo_dir=self.repo_dir, static_analysis=self.mock_analysis, system_message="Test")

        result = agent._invoke("Test prompt")

        # Callbacks should be passed to agent
        call_args = mock_agent_executor.invoke.call_args
        config = call_args[1]["config"]
        self.assertIn("callbacks", config)
        # Should have 2 callbacks: module-level MONITORING_CALLBACK and agent_monitoring_callback
        self.assertEqual(len(config["callbacks"]), 2)
        self.assertIn(agent.agent_monitoring_callback, config["callbacks"])

    @patch("agents.agent.ChatOpenAI")
    @patch("agents.agent.create_react_agent")
    @patch("agents.agent.create_extractor")
    def test_parse_invoke(self, mock_extractor, mock_create_agent, mock_chat_openai):
        # Test parse_invoke method
        mock_llm = Mock()
        mock_chat_openai.return_value = mock_llm

        mock_agent_executor = Mock()
        mock_create_agent.return_value = mock_agent_executor

        # Mock response
        mock_response_message = AIMessage(content='{"value": "test_value"}')
        mock_agent_executor.invoke.return_value = {"messages": [mock_response_message]}

        # Mock extractor
        mock_extractor_instance = Mock()
        mock_extractor.return_value = mock_extractor_instance
        mock_extractor_instance.invoke.return_value = {"responses": [{"value": "test_value"}]}

        agent = CodeBoardingAgent(repo_dir=self.repo_dir, static_analysis=self.mock_analysis, system_message="Test")

        result = agent._parse_invoke("Test prompt", TestResponse)

        # Should return parsed response
        self.assertIsInstance(result, TestResponse)
        self.assertEqual(result.value, "test_value")

    @patch("agents.agent.ChatOpenAI")
    @patch("agents.agent.create_react_agent")
    def test_get_monitoring_results_no_callback(self, mock_create_agent, mock_chat_openai):
        # Test getting monitoring results when no callback exists
        mock_llm = Mock()
        mock_chat_openai.return_value = mock_llm
        mock_create_agent.return_value = Mock()

        agent = CodeBoardingAgent(repo_dir=self.repo_dir, static_analysis=self.mock_analysis, system_message="Test")

        results = agent.get_monitoring_results()

        # Should return stats structure with zeros
        self.assertIn("token_usage", results)
        self.assertEqual(results["token_usage"]["total_tokens"], 0)
        self.assertEqual(results["token_usage"]["input_tokens"], 0)
        self.assertEqual(results["token_usage"]["output_tokens"], 0)

    @patch("agents.agent.ChatOpenAI")
    @patch("agents.agent.create_react_agent")
    def test_get_monitoring_results_with_callback(self, mock_create_agent, mock_chat_openai):
        # Test getting monitoring results with callback
        mock_llm = Mock()
        mock_chat_openai.return_value = mock_llm
        mock_create_agent.return_value = Mock()

        agent = CodeBoardingAgent(repo_dir=self.repo_dir, static_analysis=self.mock_analysis, system_message="Test")

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

    @patch("agents.agent.ChatOpenAI")
    @patch("agents.agent.create_react_agent")
    def test_setup_env_vars(self, mock_create_agent, mock_chat_openai):
        # Test environment variable setup
        mock_llm = Mock()
        mock_chat_openai.return_value = mock_llm
        mock_create_agent.return_value = Mock()

        with patch.dict(
            os.environ,
            {
                "OPENAI_API_KEY": "openai_key",
                "OPENAI_BASE_URL": "https://api.openai.com",
                "CODEBOARDING_MODEL": "gpt-4o",
            },
            clear=True,
        ):
            agent = CodeBoardingAgent(repo_dir=self.repo_dir, static_analysis=self.mock_analysis, system_message="Test")

            self.assertEqual(agent.openai_api_key, "openai_key")
            self.assertEqual(agent.openai_base_url, "https://api.openai.com")
            self.assertEqual(agent.codeboarding_model, "gpt-4o")

    @patch("agents.agent.ChatOpenAI")
    @patch("agents.agent.create_react_agent")
    def test_initialize_llm_custom_model(self, mock_create_agent, mock_chat_openai):
        # Test LLM initialization with custom model
        mock_llm = Mock()
        mock_chat_openai.return_value = mock_llm
        mock_create_agent.return_value = Mock()

        with patch.dict(os.environ, {"OPENAI_API_KEY": "test_key", "CODEBOARDING_MODEL": "gpt-4-turbo"}, clear=True):
            agent = CodeBoardingAgent(repo_dir=self.repo_dir, static_analysis=self.mock_analysis, system_message="Test")

            # Check that custom model was used
            call_args = mock_chat_openai.call_args
            self.assertEqual(call_args[1]["model"], "gpt-4-turbo")

    @patch("agents.agent.ChatOpenAI")
    @patch("agents.agent.create_react_agent")
    @patch("agents.agent.create_extractor")
    @patch("time.sleep")
    def test_parse_response_with_retry(self, mock_sleep, mock_extractor, mock_create_agent, mock_chat_openai):
        # Test parse_response with retry logic
        mock_llm = Mock()
        mock_chat_openai.return_value = mock_llm
        mock_create_agent.return_value = Mock()

        # Mock extractor to fail first, then succeed
        mock_extractor_instance = Mock()
        mock_extractor.return_value = mock_extractor_instance
        mock_extractor_instance.invoke.side_effect = [
            IndexError("First attempt fails"),
            {"responses": [{"value": "success"}]},
        ]

        agent = CodeBoardingAgent(repo_dir=self.repo_dir, static_analysis=self.mock_analysis, system_message="Test")

        result = agent._parse_response("Test prompt", '{"value": "success"}', TestResponse, max_retries=5)

        # Should succeed after retry
        self.assertIsInstance(result, TestResponse)
        self.assertEqual(result.value, "success")

    @patch("agents.agent.ChatOpenAI")
    @patch("agents.agent.create_react_agent")
    def test_tools_initialized(self, mock_create_agent, mock_chat_openai):
        # Test that all required tools are initialized
        mock_llm = Mock()
        mock_chat_openai.return_value = mock_llm
        mock_create_agent.return_value = Mock()

        agent = CodeBoardingAgent(repo_dir=self.repo_dir, static_analysis=self.mock_analysis, system_message="Test")

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

    @patch("agents.agent.ChatOpenAI")
    @patch("agents.agent.create_react_agent")
    def test_agent_created_with_tools(self, mock_create_agent, mock_chat_openai):
        # Test that agent is created with correct tools
        mock_llm = Mock()
        mock_chat_openai.return_value = mock_llm
        mock_create_agent.return_value = Mock()

        agent = CodeBoardingAgent(repo_dir=self.repo_dir, static_analysis=self.mock_analysis, system_message="Test")

        # Verify create_react_agent was called with tools
        call_args = mock_create_agent.call_args
        self.assertIn("tools", call_args[1])
        tools = call_args[1]["tools"]
        # Should have at least 5 tools
        self.assertGreaterEqual(len(tools), 5)


if __name__ == "__main__":
    unittest.main()
