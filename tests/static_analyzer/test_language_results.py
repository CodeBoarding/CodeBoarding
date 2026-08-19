"""Regression tests for per-language CFG bucket merging.

Why the subprocess: a self-merge does not raise, it allocates until the machine
dies, so an inline test would hang the suite instead of reporting.
"""

import multiprocessing
import unittest

from static_analyzer.config import NodeType
from static_analyzer.cfg import CallGraph, EdgeKind, ReferenceEdge
from static_analyzer.language_results import ControlFlowGraph
from static_analyzer.node import Node

# Long enough that a healthy merge of this tiny graph always finishes, short
# enough that a regression stays well under a gigabyte before it is killed.
_TERMINATION_TIMEOUT_SECONDS = 10


def _node(index: int) -> Node:
    return Node(f"pkg.mod.func{index}", NodeType.FUNCTION, f"src/mod{index}.ts", index * 10, index * 10 + 5)


def _graph_with_reference_edges(edge_count: int = 25) -> CallGraph:
    """A CallGraph whose reference edges all have both endpoints present.

    Endpoints must resolve to real nodes or ``add_reference_edge`` drops them
    and a self-merge would terminate for the wrong reason.
    """
    graph = CallGraph(language="typescript")
    for index in range(edge_count + 1):
        graph.add_node(_node(index))
    for index in range(edge_count):
        graph.add_reference_edge(
            ReferenceEdge(_node(index).fully_qualified_name, _node(index + 1).fully_qualified_name, EdgeKind.TYPEREF)
        )
    return graph


def _self_merge_is_a_noop() -> None:
    bucket = ControlFlowGraph()
    graph = _graph_with_reference_edges()
    expected_edges = list(graph.reference_edges)
    expected_nodes = sorted(graph.nodes)

    bucket.merge(graph)
    bucket.merge(graph)

    assert bucket.graph is graph
    assert (
        bucket.graph.reference_edges == expected_edges
    ), f"reference edges grew: {len(expected_edges)} -> {len(bucket.graph.reference_edges)}"
    assert sorted(bucket.graph.nodes) == expected_nodes


class TestControlFlowGraphMerge(unittest.TestCase):
    def test_merging_the_held_graph_again_is_a_noop(self):
        """Warm-start hands both engines of a language the same cached graph."""
        process = multiprocessing.get_context("spawn").Process(target=_self_merge_is_a_noop)
        process.start()
        process.join(_TERMINATION_TIMEOUT_SECONDS)
        if process.is_alive():
            process.kill()
            process.join()
            self.fail(
                f"self-merge did not terminate within {_TERMINATION_TIMEOUT_SECONDS}s: "
                "it is iterating a container while adding to it, and allocating without bound"
            )
        self.assertEqual(process.exitcode, 0, "self-merge changed the graph; run _self_merge_is_a_noop inline")

    def test_merging_a_subgraph_already_absorbed_is_a_noop(self):
        """Identity only catches the graph the bucket holds, not content it already has.

        A separate object whose nodes and edges are all present must add nothing,
        or repeated merges grow the bucket without bound across runs.
        """
        bucket = ControlFlowGraph()
        bucket.merge(_graph_with_reference_edges(edge_count=5))
        merged = bucket.graph
        assert merged is not None
        before = (list(merged.reference_edges), sorted(merged.nodes), len(merged.edges))

        subgraph = CallGraph(language="typescript")
        subgraph.add_node(_node(0))
        subgraph.add_node(_node(1))
        subgraph.add_reference_edge(
            ReferenceEdge(_node(0).fully_qualified_name, _node(1).fully_qualified_name, EdgeKind.TYPEREF)
        )
        bucket.merge(subgraph)

        self.assertEqual((list(merged.reference_edges), sorted(merged.nodes), len(merged.edges)), before)


if __name__ == "__main__":
    unittest.main()
