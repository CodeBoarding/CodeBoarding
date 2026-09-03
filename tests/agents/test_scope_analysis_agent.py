import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from langchain.agents.middleware import ModelCallLimitMiddleware, ToolCallLimitMiddleware
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage

from agents.agent_responses import AnalysisInsights, Component
from agents.scope_analysis_agent import MAX_SCOPE_MODEL_CALLS, MAX_SCOPE_TOOL_CALLS, ScopeAnalysisAgent
from agents.tools import MethodCallsTool, ReadFileTool
from static_analyzer.analysis_result import StaticAnalysisResults
from static_analyzer.cfg import CallGraph
from static_analyzer.clustering import ClusterGroup, ClusterScopeResult
from static_analyzer.config import Language, NodeType
from static_analyzer.node import Node


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


class TestScopeAnalysisAgent(unittest.TestCase):
    @patch("agents.scope_analysis_agent.create_agent")
    def test_exposes_only_scoped_file_and_method_tools_with_runtime_limits(self, create_agent):
        static_analysis, scope, analysis = _inputs()
        runtime = MagicMock()
        runtime.invoke.return_value = {
            "messages": [
                AIMessage(
                    content='{"description":"scope","components":['
                    '{"group_id":"1","name":"Runner","description":"Runs work","key_entities":[]}],'
                    '"relations":[]}'
                )
            ]
        }
        create_agent.return_value = runtime
        agent = ScopeAnalysisAgent(Path("/repo"), static_analysis, MagicMock(spec=BaseChatModel))

        result = agent.analyze(scope, analysis, {"1"})

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.components[0].name, "Runner")
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
        self.assertEqual(runtime.invoke.call_args.kwargs["config"]["recursion_limit"], 20)

    @patch("agents.scope_analysis_agent.create_agent")
    def test_returns_none_for_malformed_model_output(self, create_agent):
        static_analysis, scope, analysis = _inputs()
        runtime = MagicMock()
        runtime.invoke.return_value = {"messages": [AIMessage(content="not JSON")]}
        create_agent.return_value = runtime
        agent = ScopeAnalysisAgent(Path("/repo"), static_analysis, MagicMock(spec=BaseChatModel))

        self.assertIsNone(agent.analyze(scope, analysis, {"1"}))
