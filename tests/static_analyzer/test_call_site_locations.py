"""Tests for preserving call-site locations on static-analysis edges."""

from static_analyzer.constants import NodeType
from static_analyzer.graph import CallGraph, Edge
from static_analyzer.node import Node


def test_call_graph_preserves_multiple_call_sites_for_same_edge():
    graph = CallGraph()
    src = Node("module.caller", NodeType.FUNCTION, "/repo/module.py", 1, 10)
    dst = Node("module.target", NodeType.FUNCTION, "/repo/module.py", 20, 25)
    graph.add_node(src)
    graph.add_node(dst)

    graph.add_edge(
        "module.caller",
        "module.target",
        call_sites=[{"file": "/repo/module.py", "line": 3, "column": 9}],
    )
    graph.add_edge(
        "module.caller",
        "module.target",
        call_sites=[{"file": "/repo/module.py", "line": 7, "column": 13}],
    )

    assert len(graph.edges) == 1
    assert graph.edges[0].call_sites == [
        {"file": "/repo/module.py", "line": 3, "column": 9},
        {"file": "/repo/module.py", "line": 7, "column": 13},
    ]


def test_call_graph_visit_paths_rewrites_canonical_call_site_file_key():
    graph = CallGraph()
    src = Node("module.caller", NodeType.FUNCTION, "/repo/module.py", 1, 10)
    dst = Node("module.target", NodeType.FUNCTION, "/repo/module.py", 20, 25)
    graph.add_node(src)
    graph.add_node(dst)

    graph.add_edge(
        "module.caller",
        "module.target",
        call_sites=[{"file": "/repo/module.py", "line": 3, "column": 9}],
    )
    graph.visit_paths(lambda path: path.replace("/repo/", ""))

    assert graph.edges[0].call_sites == [{"file": "module.py", "line": 3, "column": 9}]
