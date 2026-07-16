from static_analyzer.constants import NodeType
from static_analyzer.program_graph import (
    ProgramEdge,
    ProgramEdgeKind,
    ProgramGraph,
    ProgramNode,
    ProgramNodeKind,
    ProgramOccurrence,
)


def _graph() -> ProgramGraph:
    graph = ProgramGraph(language="python")
    for symbol_id, line in (("module.caller", 1), ("module.target", 20)):
        graph.add_node(
            ProgramNode(
                symbol_id,
                ProgramNodeKind.SYMBOL,
                "python",
                symbol_id.rsplit(".", 1)[-1],
                "/repo/module.py",
                NodeType.FUNCTION,
                line,
                line + 5,
            )
        )
    return graph


def test_program_graph_merges_call_occurrences() -> None:
    graph = _graph()
    for line, column in ((3, 9), (7, 13)):
        graph.add_edge(
            ProgramEdge(
                ProgramEdgeKind.CALL,
                "module.caller",
                "module.target",
                [ProgramOccurrence("/repo/module.py", line, column)],
            )
        )

    assert graph.call_edges()[0].occurrence_dicts() == [
        {"file": "/repo/module.py", "line": 3, "column": 9},
        {"file": "/repo/module.py", "line": 7, "column": 13},
    ]


def test_visit_paths_rewrites_call_occurrences() -> None:
    graph = _graph()
    graph.add_edge(
        ProgramEdge(
            ProgramEdgeKind.CALL,
            "module.caller",
            "module.target",
            [ProgramOccurrence("/repo/module.py", 3, 9)],
        )
    )

    graph.visit_paths(lambda path: path.replace("/repo/", ""))

    assert graph.call_edges()[0].occurrence_dicts() == [{"file": "module.py", "line": 3, "column": 9}]
