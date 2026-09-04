from pathlib import Path

from agents.tools import MethodCallsTool
from agents.tools.base import RepoContext
from static_analyzer.analysis_result import StaticAnalysisResults
from static_analyzer.cfg import CallGraph
from static_analyzer.config import Language, NodeType
from static_analyzer.node import Node


def _analysis() -> tuple[StaticAnalysisResults, CallGraph]:
    graph = CallGraph(language="python")
    graph.add_node(Node("pkg.a", NodeType.FUNCTION, "a.py", 1, 2))
    graph.add_node(Node("pkg.b", NodeType.FUNCTION, "b.py", 1, 2))
    graph.add_node(Node("pkg.outside", NodeType.FUNCTION, "outside.py", 1, 2))
    graph.add_edge("pkg.a", "pkg.b")
    graph.add_edge("pkg.a", "pkg.outside")
    analysis = StaticAnalysisResults()
    analysis.add_cfg(Language.PYTHON, graph)
    return analysis, graph


def test_returns_immediate_calls_in_either_direction() -> None:
    analysis, _ = _analysis()
    tool = MethodCallsTool(context=RepoContext(repo_dir=Path("."), static_analysis=analysis))

    assert tool._run("pkg.a", "outgoing") == "pkg.a -> pkg.b\npkg.a -> pkg.outside"
    assert tool._run("pkg.b", "incoming") == "pkg.a -> pkg.b"


def test_reports_missing_static_analysis() -> None:
    tool = MethodCallsTool(context=RepoContext(repo_dir=Path(".")))

    assert tool._run("pkg.a", "outgoing") == "No static analysis data available."


def test_scope_restriction_filters_endpoints_and_queries() -> None:
    analysis, graph = _analysis()
    context = RepoContext(
        repo_dir=Path("."),
        static_analysis=analysis,
        scope_restricted=True,
        scope_methods=frozenset({"pkg.a", "pkg.b"}),
        cfg_graphs={"python": graph},
    )
    tool = MethodCallsTool(context=context)

    assert tool._run("pkg.a", "outgoing") == "pkg.a -> pkg.b"
    assert tool._run("pkg.b", "incoming") == "pkg.a -> pkg.b"
    assert "outside the current analysis scope" in tool._run("pkg.outside", "incoming")
