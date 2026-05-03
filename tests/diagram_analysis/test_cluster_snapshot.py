"""Tests for ``diagram_analysis.cluster_snapshot``.

The snapshot is now reconstructed from ``analysis.json`` (specifically each
``Component.cluster_members``), so the round-trip we exercise here is
``Component.cluster_members -> ClusterSnapshot -> ClusterSnapshotEntry``
classified by language using a freshly-built CFG.
"""

import unittest

from agents.agent_responses import (
    AnalysisInsights,
    Component,
    FileMethodGroup,
    MethodEntry,
)
from diagram_analysis.cluster_snapshot import (
    ClusterSnapshot,
    ClusterSnapshotEntry,
    snapshot_from_analysis,
    snapshot_from_cluster_results,
)
from static_analyzer.analysis_result import StaticAnalysisResults
from static_analyzer.constants import NodeType
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
        results.add_cfg(language, graph)
    return results


def _component(component_id: str, name: str, cluster_members: dict[int, list[str]]) -> Component:
    return Component(
        name=name,
        description="",
        key_entities=[],
        component_id=component_id,
        source_cluster_ids=sorted(cluster_members.keys()),
        cluster_members=cluster_members,
    )


def _analysis(components: list[Component]) -> AnalysisInsights:
    return AnalysisInsights(description="", components=components, components_relations=[])


class TestSnapshotFromAnalysis(unittest.TestCase):
    def test_round_trip_partitions_by_language(self) -> None:
        py_graph = _build_graph([("a.foo", "a.py"), ("a.bar", "a.py"), ("b.baz", "b.py")])
        go_graph = _build_graph([("c.qux", "c.go")])
        static = _build_static({"python": py_graph, "go": go_graph})

        comp_root = _component("1", "Auth", cluster_members={1: ["a.foo", "a.bar"], 2: ["b.baz"]})
        comp_root_go = _component("2", "Worker", cluster_members={3: ["c.qux"]})
        root = _analysis([comp_root, comp_root_go])

        snap = snapshot_from_analysis(root, {}, static)

        self.assertEqual(set(snap.by_language), {"python", "go"})
        py = snap.by_language["python"]
        self.assertEqual(py[1].members, {"a.foo", "a.bar"})
        self.assertEqual(py[1].files, {"a.py"})
        self.assertEqual(py[2].members, {"b.baz"})
        self.assertEqual(py[2].files, {"b.py"})
        self.assertEqual(snap.by_language["go"][3].members, {"c.qux"})
        self.assertEqual(snap.by_language["go"][3].files, {"c.go"})

    def test_sub_analyses_contribute_their_own_cluster_members(self) -> None:
        graph = _build_graph(
            [("root.a", "root.py"), ("sub.x", "sub.py"), ("sub.y", "sub.py")],
        )
        static = _build_static({"python": graph})

        root = _analysis([_component("1", "Top", cluster_members={1: ["root.a"]})])
        sub = _analysis([_component("1.1", "Detail", cluster_members={2: ["sub.x", "sub.y"]})])

        snap = snapshot_from_analysis(root, {"1": sub}, static)

        py = snap.by_language["python"]
        self.assertEqual(py[1].members, {"root.a"})
        self.assertEqual(py[2].members, {"sub.x", "sub.y"})
        self.assertEqual(py[2].files, {"sub.py"})

    def test_members_missing_from_cfg_are_skipped(self) -> None:
        # Only "a.foo" remains in the new CFG; "a.deleted" is gone from source.
        graph = _build_graph([("a.foo", "a.py")])
        static = _build_static({"python": graph})

        root = _analysis([_component("1", "Auth", cluster_members={1: ["a.foo", "a.deleted"]})])

        snap = snapshot_from_analysis(root, {}, static)

        self.assertEqual(snap.by_language["python"][1].members, {"a.foo"})

    def test_empty_when_no_cluster_members(self) -> None:
        graph = _build_graph([("a.foo", "a.py")])
        static = _build_static({"python": graph})

        root = _analysis([_component("1", "Auth", cluster_members={})])

        snap = snapshot_from_analysis(root, {}, static)

        self.assertEqual(snap.all_cluster_ids(), set())

    def test_components_sharing_a_cluster_id_are_merged(self) -> None:
        # Real analyses won't normally do this — every cluster is owned by
        # exactly one component — but the merge behaviour is well-defined and
        # protects us from accidental double-counting if the invariant slips.
        graph = _build_graph([("a.x", "a.py"), ("a.y", "a.py")])
        static = _build_static({"python": graph})

        root = _analysis(
            [
                _component("1", "Alpha", cluster_members={7: ["a.x"]}),
                _component("2", "Beta", cluster_members={7: ["a.y"]}),
            ]
        )

        snap = snapshot_from_analysis(root, {}, static)

        self.assertEqual(snap.by_language["python"][7].members, {"a.x", "a.y"})


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


# Suppress unused-import warning: kept for type-aware test authoring tools.
_ = (FileMethodGroup, MethodEntry)


if __name__ == "__main__":
    unittest.main()
