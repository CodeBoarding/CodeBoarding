import unittest
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

from langchain.agents.middleware import ModelCallLimitMiddleware, ToolCallLimitMiddleware
from langchain.agents.structured_output import ToolStrategy
from langchain_core.language_models import BaseChatModel, LanguageModelInput
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage
from langchain_core.runnables import Runnable
from langchain_core.tools import BaseTool

from agents.agent_responses import AnalysisInsights, Component
from agents.llm_errors import LLMAuthError
from agents.scope_analysis_agent import (
    MAX_SCOPE_MODEL_CALLS,
    MAX_SCOPE_TOOL_CALLS,
    SCOPE_RECURSION_LIMIT,
    ScopeAnalysisAgent,
    ScopeAnalysisResult,
    ScopeComponentSemantics,
)
from agents.tools import MethodCallsTool, ReadFileTool
from static_analyzer.analysis_result import StaticAnalysisResults
from static_analyzer.cfg import CallGraph
from static_analyzer.clustering import ClusterGroup, ClusterScopeResult
from static_analyzer.config import Language, NodeType
from static_analyzer.node import Node


class ToolCallingFakeModel(FakeMessagesListChatModel):
    """Script tool calls while accepting LangChain's tool binding."""

    def bind_tools(
        self,
        tools: Sequence[dict[str, Any] | type | Callable | BaseTool],
        *,
        tool_choice: str | None = None,
        **kwargs: Any,
    ) -> Runnable[LanguageModelInput, AIMessage]:
        return self


def _inputs() -> tuple[StaticAnalysisResults, ClusterScopeResult, AnalysisInsights]:
    graph = CallGraph(language="python")
    graph.add_node(Node("pkg.run", NodeType.FUNCTION, "pkg.py", 1, 3))
    static_analysis = StaticAnalysisResults()
    static_analysis.add_cfg(Language.PYTHON, graph)
    scope = ClusterScopeResult(
        scope_id="root",
        graphs_by_language={"python": graph},
        groups=[
            ClusterGroup(
                group_id="1",
                cluster_ids=[1],
                symbol_members_by_language={"python": {"pkg.run"}},
            )
        ],
    )
    analysis = AnalysisInsights(
        description="",
        components=[Component(name="Component 1", description="", key_entities=[], component_id="1")],
        components_relations=[],
    )
    return static_analysis, scope, analysis


def _answer() -> ScopeAnalysisResult:
    return ScopeAnalysisResult(
        description="scope",
        components=[ScopeComponentSemantics(group_id="1", name="Runner", description="Runs work")],
        relations=[],
    )


class TestScopeAnalysisAgent(unittest.TestCase):
    @patch("agents.scope_analysis_agent.create_agent")
    def test_exposes_only_scoped_file_and_method_tools_with_runtime_limits(self, create_agent):
        static_analysis, scope, analysis = _inputs()
        runtime = MagicMock()
        runtime.invoke.return_value = {"messages": [AIMessage(content="")], "structured_response": _answer()}
        create_agent.return_value = runtime
        agent = ScopeAnalysisAgent(Path("/repo"), static_analysis, MagicMock(spec=BaseChatModel))

        result = agent.analyze(scope, analysis, {"1"})

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.components[0].name, "Runner")
        response_format = create_agent.call_args.kwargs["response_format"]
        self.assertIsInstance(response_format, ToolStrategy)
        self.assertIs(response_format.schema, ScopeAnalysisResult)
        tools = create_agent.call_args.kwargs["tools"]
        self.assertEqual([type(tool) for tool in tools], [ReadFileTool, MethodCallsTool])
        self.assertEqual([tool.name for tool in tools], ["readFile", "getMethodCalls"])
        self.assertTrue(all(tool.context.scope_restricted for tool in tools))
        self.assertEqual(tools[0].context.scope_files, frozenset({"pkg.py"}))
        self.assertEqual(tools[1].context.scope_methods, frozenset({"pkg.run"}))
        self.assertEqual(set(tools[1].context.cfg_graphs), {"python"})
        middleware = create_agent.call_args.kwargs["middleware"]
        tool_limit = next(item for item in middleware if isinstance(item, ToolCallLimitMiddleware))
        model_limit = next(item for item in middleware if isinstance(item, ModelCallLimitMiddleware))
        self.assertEqual(tool_limit.run_limit, MAX_SCOPE_TOOL_CALLS)
        self.assertEqual(model_limit.run_limit, MAX_SCOPE_MODEL_CALLS)
        self.assertEqual(runtime.invoke.call_args.kwargs["config"]["recursion_limit"], SCOPE_RECURSION_LIMIT)

    @patch("agents.scope_analysis_agent.create_agent")
    def test_converts_provider_authentication_failures(self, create_agent):
        class AuthenticationError(Exception):
            status_code = 401

        static_analysis, scope, analysis = _inputs()
        runtime = MagicMock()
        runtime.invoke.side_effect = AuthenticationError("invalid API key")
        create_agent.return_value = runtime
        agent = ScopeAnalysisAgent(Path("/repo"), static_analysis, MagicMock(spec=BaseChatModel))

        with self.assertRaises(LLMAuthError):
            agent.analyze(scope, analysis, {"1"})

        runtime.invoke.assert_called_once()

    @patch.object(ReadFileTool, "_run", return_value="source")
    def test_graph_ceiling_allows_tool_budget_to_terminate_the_run(self, read_file):
        static_analysis, scope, analysis = _inputs()
        tool_requests = [
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "readFile",
                        "args": {"file_path": "pkg.py", "line_number": 1},
                        "id": f"read-{index}",
                        "type": "tool_call",
                    }
                ],
            )
            for index in range(MAX_SCOPE_TOOL_CALLS + 1)
        ]
        final_response = AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "ScopeAnalysisResult",
                    "args": _answer().model_dump(),
                    "id": "final",
                    "type": "tool_call",
                }
            ],
        )
        model = ToolCallingFakeModel(responses=[*tool_requests, final_response])

        result = ScopeAnalysisAgent(Path("/repo"), static_analysis, model).analyze(scope, analysis, {"1"})

        self.assertIsNotNone(result)
        self.assertEqual(read_file.call_count, MAX_SCOPE_TOOL_CALLS)

    @patch("agents.scope_analysis_agent.create_agent")
    def test_returns_none_when_the_run_ended_without_a_structured_answer(self, create_agent):
        static_analysis, scope, analysis = _inputs()
        runtime = MagicMock()
        runtime.invoke.return_value = {"messages": [AIMessage(content="Done.")], "structured_response": None}
        create_agent.return_value = runtime
        agent = ScopeAnalysisAgent(Path("/repo"), static_analysis, MagicMock(spec=BaseChatModel))

        self.assertIsNone(agent.analyze(scope, analysis, {"1"}))

    @patch("agents.scope_analysis_agent.create_agent")
    def test_passes_the_enclosing_names_to_the_renderer(self, create_agent):
        static_analysis, scope, analysis = _inputs()
        runtime = MagicMock()
        runtime.invoke.return_value = {"messages": [], "structured_response": _answer()}
        create_agent.return_value = runtime
        agent = ScopeAnalysisAgent(Path("/repo"), static_analysis, MagicMock(spec=BaseChatModel))

        agent.analyze(scope, analysis, {"1"}, enclosing_names=("Engine",))

        prompt = runtime.invoke.call_args.args[0]["messages"][0].content
        self.assertIn('"enclosing_components": [\n    "Engine"\n  ]', prompt)
