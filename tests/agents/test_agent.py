import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, MagicMock, patch

from langchain_core.messages import AIMessage
from langchain_core.language_models import BaseChatModel
from pydantic import BaseModel

from agents.agent import CodeBoardingAgent
from agents.agent_responses import AnalysisInsights, ClusterAnalysis, ClustersComponent, Relation
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
        # Should have 2 callbacks: module-level MONITORING_CALLBACK and agent_monitoring_callback
        self.assertEqual(len(config["callbacks"]), 2)
        self.assertIn(agent.agent_monitoring_callback, config["callbacks"])

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
        self.assertIsNotNone(agent.component_bridge_edges_tool)
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
        prompt = ClustersComponent.extractor_str()
        for field in ("existing_component_id", "parent_id", "redetail_needed"):
            self.assertNotIn(field, prompt)

    def test_extractor_str_shows_hidden_fields_when_requested(self):
        prompt = ClustersComponent.extractor_str(include_hidden=True)
        for field in ("existing_component_id", "parent_id", "redetail_needed"):
            self.assertIn(field, prompt)

    def test_model_json_schema_hides_hidden_fields_by_default(self):
        schema = ClustersComponent.model_json_schema()
        props = schema.get("properties", {})
        for field in ("existing_component_id", "parent_id", "redetail_needed"):
            self.assertNotIn(field, props)
        defs = schema.get("$defs", {}).get("ClustersComponent", {}).get("properties", {})
        for field in ("existing_component_id", "parent_id", "redetail_needed"):
            self.assertNotIn(field, defs)

    def test_model_json_schema_shows_hidden_fields_when_requested(self):
        schema = ClustersComponent.model_json_schema(include_hidden=True)
        props = schema.get("properties", {})
        for field in ("existing_component_id", "parent_id", "redetail_needed"):
            self.assertIn(field, props)

    def test_relation_static_evidence_hidden_from_llm_schema_by_default(self):
        schema = AnalysisInsights.model_json_schema()
        relation_props = schema.get("$defs", {}).get("Relation", {}).get("properties", {})
        for field in ("src_id", "dst_id", "is_static", "all_edges"):
            self.assertNotIn(field, relation_props)

        prompt = Relation.extractor_str()
        for field in ("src_id", "dst_id", "is_static", "all_edges"):
            self.assertNotIn(field, prompt)

    def test_relation_static_evidence_available_when_hidden_fields_requested(self):
        schema = AnalysisInsights.model_json_schema(include_hidden=True)
        relation_props = schema.get("$defs", {}).get("Relation", {}).get("properties", {})
        for field in ("src_id", "dst_id", "is_static", "all_edges"):
            self.assertIn(field, relation_props)

        prompt = Relation.extractor_str(include_hidden=True)
        for field in ("src_id", "dst_id", "is_static", "all_edges"):
            self.assertIn(field, prompt)
        for field in ("source", "target", "description"):
            self.assertIn(field, prompt)

    def test_parse_response_uses_hidden_schema_for_structured_parse(self):
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
