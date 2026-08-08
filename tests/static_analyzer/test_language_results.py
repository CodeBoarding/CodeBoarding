"""Regression tests for per-language CFG bucket merging.

Why the subprocess: a self-merge does not raise, it allocates until the machine
dies, so an inline test would hang the suite instead of reporting.
"""

import multiprocessing
import unittest

from static_analyzer.analysis_result import StaticAnalysisResults
from static_analyzer.constants import Language, NodeType
from static_analyzer.graph import CallGraph, EdgeKind
from static_analyzer.language_results import ControlFlowGraph
from static_analyzer.node import Node

# Long enough that a healthy merge of this tiny graph always finishes, short
# enough that a regression stays well under a gigabyte before it is killed.
_TERMINATION_TIMEOUT_SECONDS = 10


def _merged_graph(bucket: ControlFlowGraph) -> CallGraph:
    """The graph a merged bucket holds, narrowed from ``CallGraph | None``."""
    graph = bucket.graph
    assert graph is not None, "bucket holds no graph after a merge"
    return graph


def _graph_with_reference_edges(edge_count: int = 25) -> CallGraph:
    """A CallGraph whose reference edges all have both endpoints present.

    Endpoints must resolve to real nodes or ``add_reference_edge`` drops them
    and a self-merge would terminate for the wrong reason.
    """
    graph = CallGraph(language="typescript")
    for index in range(edge_count + 1):
        graph.add_node(
            Node(f"pkg.mod.func{index}", NodeType.FUNCTION, f"src/mod{index}.ts", index * 10, index * 10 + 5)
        )
    for index in range(edge_count):
        graph.add_reference_edge(f"pkg.mod.func{index}", f"pkg.mod.func{index + 1}", EdgeKind.TYPEREF)
    return graph


# --- child-process bodies: each returns normally or raises AssertionError ---


def _self_merge_is_a_noop() -> None:
    """The reported shape: one bucket, one graph, merged twice."""
    bucket = ControlFlowGraph()
    graph = _graph_with_reference_edges()
    expected_edges = list(graph.reference_edges)
    expected_nodes = sorted(graph.nodes)

    bucket.merge(graph)  # first engine: bucket adopts the object
    bucket.merge(graph)  # second engine of the same language: same object again

    assert bucket.graph is graph, "bucket should still hold the adopted graph"
    assert (
        bucket.graph.reference_edges == expected_edges
    ), f"self-merge changed reference edges: {len(expected_edges)} -> {len(bucket.graph.reference_edges)}"
    assert sorted(bucket.graph.nodes) == expected_nodes, "self-merge changed the node set"


def _add_cfg_twice_is_a_noop() -> None:
    """The warm-start shape: one language, two engines, one cached graph."""
    results = StaticAnalysisResults()
    graph = _graph_with_reference_edges()
    expected_edges = list(graph.reference_edges)

    results.add_cfg(Language.TYPESCRIPT, graph)
    results.add_cfg(Language.TYPESCRIPT, graph)

    assert results.get_cfg(Language.TYPESCRIPT).reference_edges == expected_edges


def _equal_but_distinct_graphs_merge() -> None:
    """Identity, not equality, is what makes a merge degenerate."""
    bucket = ControlFlowGraph()
    bucket.merge(_graph_with_reference_edges(edge_count=3))
    bucket.merge(_graph_with_reference_edges(edge_count=3))


class _GuardedMergeCase(unittest.TestCase):
    """Runs a merge body in a subprocess so a regression cannot hang the suite."""

    def assert_completes(self, target) -> None:
        # "spawn", not the default fork: pytest is multi-threaded by the time
        # these run, and forking a multi-threaded parent risks a child deadlock
        # that would look like the very hang under test.
        process = multiprocessing.get_context("spawn").Process(target=target)
        process.start()
        process.join(_TERMINATION_TIMEOUT_SECONDS)
        if process.is_alive():
            process.kill()
            process.join()
            self.fail(
                f"{target.__name__} did not terminate within {_TERMINATION_TIMEOUT_SECONDS}s: "
                "merging a CFG bucket into itself is looping and allocating without bound"
            )
        self.assertEqual(
            process.exitcode,
            0,
            f"{target.__name__} failed in the child process (exit code {process.exitcode}); "
            "re-run its body inline to see the assertion",
        )


class TestControlFlowGraphSelfMerge(_GuardedMergeCase):
    """A bucket merged with the object it already holds must be a no-op."""

    def test_self_merge_terminates_and_changes_nothing(self):
        self.assert_completes(_self_merge_is_a_noop)

    def test_add_cfg_twice_with_same_object_is_a_noop(self):
        self.assert_completes(_add_cfg_twice_is_a_noop)

    def test_equal_but_distinct_graphs_still_merge(self):
        self.assert_completes(_equal_but_distinct_graphs_merge)


class TestControlFlowGraphDistinctMerge(unittest.TestCase):
    """The self-merge guard must not short-circuit genuine merges.

    These terminate with or without the fix, so they run inline for readable
    failures.
    """

    def test_distinct_graph_contributes_its_reference_edges(self):
        bucket = ControlFlowGraph()
        first = CallGraph(language="typescript")
        first.add_node(Node("pkg.a.one", NodeType.FUNCTION, "src/a.ts", 1, 5))
        first.add_node(Node("pkg.a.two", NodeType.FUNCTION, "src/a.ts", 6, 10))
        first.add_reference_edge("pkg.a.one", "pkg.a.two", EdgeKind.TYPEREF)

        second = CallGraph(language="typescript")
        second.add_node(Node("pkg.b.one", NodeType.FUNCTION, "src/b.ts", 1, 5))
        second.add_node(Node("pkg.b.two", NodeType.FUNCTION, "src/b.ts", 6, 10))
        second.add_reference_edge("pkg.b.one", "pkg.b.two", EdgeKind.TYPEREF)

        bucket.merge(first)
        bucket.merge(second)

        merged = _merged_graph(bucket)
        self.assertIn(("pkg.b.one", "pkg.b.two", str(EdgeKind.TYPEREF)), merged.reference_edges)
        self.assertIn("pkg.a.one", merged.nodes)
        self.assertIn("pkg.b.one", merged.nodes)

    def test_remerging_an_already_merged_graph_does_not_duplicate_reference_edges(self):
        """A bucket knows the graph it holds, not the ones it has already absorbed.

        Handing back a graph merged earlier does not match identity against the
        held graph, so the merge itself has to be repeat-safe.
        """
        bucket = ControlFlowGraph()
        bucket.merge(_graph_with_reference_edges(edge_count=3))

        second = CallGraph(language="typescript")
        second.add_node(Node("pkg.b.one", NodeType.FUNCTION, "src/b.ts", 1, 5))
        second.add_node(Node("pkg.b.two", NodeType.FUNCTION, "src/b.ts", 6, 10))
        second.add_reference_edge("pkg.b.one", "pkg.b.two", EdgeKind.TYPEREF)

        bucket.merge(second)
        after_first_merge = list(_merged_graph(bucket).reference_edges)
        bucket.merge(second)

        self.assertEqual(_merged_graph(bucket).reference_edges, after_first_merge)

    def test_remerging_an_already_merged_graph_leaves_nodes_and_call_edges_stable(self):
        """Nodes and call edges are keyed, so only reference edges should drift."""
        bucket = ControlFlowGraph()
        bucket.merge(_graph_with_reference_edges(edge_count=3))

        second = CallGraph(language="typescript")
        second.add_node(Node("pkg.b.one", NodeType.FUNCTION, "src/b.ts", 1, 5))
        second.add_node(Node("pkg.b.two", NodeType.FUNCTION, "src/b.ts", 6, 10))
        second.add_edge("pkg.b.one", "pkg.b.two")

        bucket.merge(second)
        merged = _merged_graph(bucket)
        node_count, edge_count = len(merged.nodes), len(merged.edges)
        bucket.merge(second)

        self.assertEqual(len(merged.nodes), node_count)
        self.assertEqual(len(merged.edges), edge_count)

    def test_distinct_graph_contributes_its_call_edges(self):
        bucket = ControlFlowGraph()
        first = CallGraph(language="typescript")
        first.add_node(Node("pkg.a.one", NodeType.FUNCTION, "src/a.ts", 1, 5))
        bucket.merge(first)

        second = CallGraph(language="typescript")
        second.add_node(Node("pkg.b.one", NodeType.FUNCTION, "src/b.ts", 1, 5))
        second.add_node(Node("pkg.b.two", NodeType.FUNCTION, "src/b.ts", 6, 10))
        second.add_edge("pkg.b.one", "pkg.b.two")
        bucket.merge(second)

        merged = _merged_graph(bucket)
        self.assertEqual(len(merged.edges), 1)
        self.assertEqual(len(merged.nodes), 3)


class TestSourceFilesBucket(unittest.TestCase):
    """``SourceFiles.extend`` is a bare list extend, fed once per engine config.

    Same shape as the CFG bug: on warm-start both TypeScript engines are handed
    the same cached file list, so the bucket ends at twice its real size -- and
    that result is written back to the pkl, so it doubles again next run.
    """

    def test_adding_the_same_source_files_twice_does_not_duplicate(self):
        results = StaticAnalysisResults()
        files = ["src/a.ts", "src/b.ts"]

        results.add_source_files(Language.TYPESCRIPT, files)
        results.add_source_files(Language.TYPESCRIPT, files)

        self.assertEqual(results.get_source_files(Language.TYPESCRIPT), files)


if __name__ == "__main__":
    unittest.main()
