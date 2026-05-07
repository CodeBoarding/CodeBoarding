"""Tests for ``diagram_analysis.cluster_snapshot``.

The snapshot is sourced exclusively from each per-language CFG's
``CallGraph._cluster_cache`` (the partition is round-tripped through the
SHA-tagged pkl). Languages without a populated cache contribute nothing,
which is what triggers the full-analysis fallback in
``DiagramGenerator.generate_analysis_incremental``.
"""

import unittest

from diagram_analysis.cluster_snapshot import (
    ClusterSnapshot,
    ClusterSnapshotEntry,
    snapshot_from_cluster_results,
    snapshot_from_static_analysis,
)
from static_analyzer.analysis_result import StaticAnalysisResults
from static_analyzer.constants import Language, NodeType
from static_analyzer.graph import CallGraph, ClusterResult
from static_analyzer.node import Node


def _build_graph(node_specs: list[tuple[str, str]]) -> CallGraph:
    """Build a single-language CFG from (qname, file) pairs."""
    graph = CallGraph(language="python")
    for idx, (fqn, fp) in enumerate(node_specs):
        graph.add_node(
            Node(
                fully_qualified_name=fqn,
                node_type=NodeType.FUNCTION,
                file_path=fp,
                line_start=idx * 10,
                line_end=idx * 10 + 1,
            )
        )
    return graph


def _build_static(graphs: dict[str, CallGraph]) -> StaticAnalysisResults:
    results = StaticAnalysisResults()
    for language, graph in graphs.items():
        results.add_cfg(Language(language), graph)
    return results


class TestSnapshotFromStaticAnalysis(unittest.TestCase):
    def test_partition_read_from_cfg_cache(self) -> None:
        graph = _build_graph([("a.foo", "a.py"), ("a.bar", "a.py"), ("b.baz", "b.py")])
        graph._cluster_cache = ClusterResult(
            clusters={5: {"a.foo", "a.bar"}, 6: {"b.baz"}},
            cluster_to_files={5: {"a.py"}, 6: {"b.py"}},
            file_to_clusters={"a.py": {5}, "b.py": {6}},
        )
        static = _build_static({"python": graph})

        snap = snapshot_from_static_analysis(static)

        py = snap.by_language["python"]
        self.assertEqual(set(py.keys()), {5, 6})
        self.assertEqual(py[5].members, {"a.foo", "a.bar"})
        self.assertEqual(py[5].files, {"a.py"})
        self.assertEqual(py[5].member_files, {"a.foo": "a.py", "a.bar": "a.py"})
        self.assertEqual(py[6].members, {"b.baz"})

    def test_partitions_each_language_independently(self) -> None:
        py_graph = _build_graph([("a.foo", "a.py"), ("b.baz", "b.py")])
        py_graph._cluster_cache = ClusterResult(clusters={1: {"a.foo"}, 2: {"b.baz"}})
        go_graph = _build_graph([("c.qux", "c.go")])
        go_graph._cluster_cache = ClusterResult(clusters={3: {"c.qux"}})
        static = _build_static({"python": py_graph, "go": go_graph})

        snap = snapshot_from_static_analysis(static)

        self.assertEqual(set(snap.by_language), {"python", "go"})
        self.assertEqual(snap.by_language["python"][1].members, {"a.foo"})
        self.assertEqual(snap.by_language["python"][2].members, {"b.baz"})
        self.assertEqual(snap.by_language["go"][3].members, {"c.qux"})

    def test_language_without_cluster_cache_is_skipped(self) -> None:
        # Why: legacy pkl / first-ever run leaves ``_cluster_cache`` as None.
        # ``generate_analysis_incremental`` checks ``all_cluster_ids()`` and
        # falls back to a full run, which then warms the pkl. The empty
        # snapshot here is the explicit "I have nothing to compare against"
        # signal, not an error.
        graph = _build_graph([("a.foo", "a.py")])  # _cluster_cache is None
        static = _build_static({"python": graph})

        snap = snapshot_from_static_analysis(static)

        self.assertEqual(snap.all_cluster_ids(), set())

    def test_qnames_outside_cfg_are_dropped_from_member_files(self) -> None:
        # A qname can appear in the cluster cache without a corresponding CFG
        # node when the cache was saved before a node-level mutation. Such
        # qnames have no authoritative file_path, so they're absorbed into
        # ``members`` but contribute nothing to ``files`` / ``member_files``.
        graph = _build_graph([("a.foo", "a.py")])
        graph._cluster_cache = ClusterResult(clusters={1: {"a.foo", "ghost.fn"}})
        static = _build_static({"python": graph})

        snap = snapshot_from_static_analysis(static)

        py = snap.by_language["python"]
        self.assertEqual(py[1].members, {"a.foo", "ghost.fn"})
        self.assertEqual(py[1].files, {"a.py"})
        self.assertEqual(py[1].member_files, {"a.foo": "a.py"})


class TestSnapshotFromClusterResults(unittest.TestCase):
    def test_in_memory_build_from_cluster_results(self) -> None:
        results = {
            "python": ClusterResult(
                clusters={1: {"a.foo", "a.bar"}, 2: {"b.baz"}},
                cluster_to_files={1: {"a.py"}, 2: {"b.py"}},
                file_to_clusters={"a.py": {1}, "b.py": {2}},
            )
        }
        snap = snapshot_from_cluster_results(results)
        self.assertEqual(snap.by_language["python"][1].members, {"a.foo", "a.bar"})
        self.assertEqual(snap.by_language["python"][1].files, {"a.py"})
        self.assertEqual(snap.by_language["python"][2].members, {"b.baz"})


class TestClusterSnapshotHelpers(unittest.TestCase):
    def test_get_language_returns_empty_for_missing_language(self) -> None:
        snap = ClusterSnapshot(by_language={"python": {1: ClusterSnapshotEntry(members={"a"})}})
        self.assertEqual(snap.get_language("rust"), {})

    def test_all_cluster_ids_aggregates_across_languages(self) -> None:
        snap = ClusterSnapshot(
            by_language={
                "python": {1: ClusterSnapshotEntry(), 2: ClusterSnapshotEntry()},
                "go": {3: ClusterSnapshotEntry()},
            }
        )
        self.assertEqual(snap.all_cluster_ids(), {1, 2, 3})


if __name__ == "__main__":
    unittest.main()
