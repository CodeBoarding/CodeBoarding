"""Tests for ``diagram_analysis.cluster_delta`` (Flavor B + threshold fallback)."""

import unittest

from diagram_analysis.cluster_delta import (
    FULL_RECLUSTER_THRESHOLD,
    JACCARD_MATCH_THRESHOLD,
    ClusterDelta,
    LanguageDelta,
    _flavor_a_fallback,
    _flavor_b_iterative,
    _jaccard,
    _match_by_jaccard,
    compute_cluster_delta,
)
from diagram_analysis.cluster_snapshot import ClusterSnapshot, ClusterSnapshotEntry, snapshot_from_cluster_results
from static_analyzer.analysis_result import StaticAnalysisResults
from static_analyzer.constants import NodeType
from static_analyzer.graph import CallGraph, ClusterResult
from static_analyzer.node import Node


def _build_graph(node_specs: list[tuple[str, str]], edges: list[tuple[str, str]]) -> CallGraph:
    """Build a CallGraph from (qname, file) pairs.

    Distinct line ranges per qname avoid the location-dedup logic in
    ``CallGraph.add_node`` that collapses entries sharing
    ``(file, line_start, line_end, type)``.
    """
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
    for src, dst in edges:
        graph.add_edge(src, dst)
    return graph


def _build_static(graph: CallGraph) -> StaticAnalysisResults:
    results = StaticAnalysisResults()
    results.add_cfg("python", graph)
    return results


def _snapshot(by_language: dict[str, dict[int, ClusterSnapshotEntry]]) -> ClusterSnapshot:
    return ClusterSnapshot(by_language=by_language)


class TestFlavorB(unittest.TestCase):
    def test_no_diff_yields_empty_delta(self) -> None:
        # Two clusters that fresh ``build_all_cluster_results`` will reproduce
        # exactly: each has >=2 connected members so neither is dropped as a
        # singleton, and the snapshot mirrors what a real prior run would have
        # written. With Fix B in place the delta universe is the union of
        # clusterable members on both sides — matching baselines round-trip
        # to ``has_changes=False``.
        graph = _build_graph(
            [("a.foo", "a.py"), ("a.bar", "a.py"), ("b.baz", "b.py"), ("b.qux", "b.py")],
            [("a.foo", "a.bar"), ("b.baz", "b.qux")],
        )
        snap = _snapshot(
            {
                "python": {
                    1: ClusterSnapshotEntry(members={"a.foo", "a.bar"}),
                    2: ClusterSnapshotEntry(members={"b.baz", "b.qux"}),
                }
            }
        )

        delta = compute_cluster_delta(snap, _build_static(graph))

        self.assertFalse(delta.has_changes)
        ld = delta.by_language["python"]
        self.assertEqual(ld.new_cluster_ids, set())
        self.assertEqual(ld.changed_cluster_ids, set())
        self.assertEqual(ld.dropped_cluster_ids, set())
        self.assertFalse(ld.fallback_used)

    def test_added_node_routed_to_neighbor_cluster(self) -> None:
        # Snapshot lists clusters that fresh clustering will reproduce; "a.new"
        # is the only added node and joins cluster 1 via its edge to a.foo.
        graph = _build_graph(
            [
                ("a.foo", "a.py"),
                ("a.bar", "a.py"),
                ("a.new", "a.py"),
                ("b.baz", "b.py"),
                ("b.qux", "b.py"),
            ],
            [("a.foo", "a.bar"), ("a.new", "a.foo"), ("b.baz", "b.qux")],
        )
        snap = _snapshot(
            {
                "python": {
                    1: ClusterSnapshotEntry(members={"a.foo", "a.bar"}),
                    2: ClusterSnapshotEntry(members={"b.baz", "b.qux"}),
                }
            }
        )

        delta = compute_cluster_delta(snap, _build_static(graph))

        self.assertTrue(delta.has_changes)
        ld = delta.by_language["python"]
        self.assertEqual(ld.new_cluster_ids, set())
        self.assertEqual(ld.changed_cluster_ids, {1})
        self.assertEqual(ld.dropped_cluster_ids, set())
        self.assertIn("a.new", ld.cluster_results.clusters[1])

    def test_isolated_added_nodes_form_new_cluster(self) -> None:
        # Snapshot mirrors a fully-connected 12-node "a" cluster; refresh adds
        # a 3-node connected "z" community (3/15 added = 20% < threshold) so
        # the routing path runs Flavor B and Louvain promotes "z.*" to a
        # fresh cluster id.
        a_nodes = [(f"a.fn{i}", "a.py") for i in range(12)]
        a_edges = [(f"a.fn{i}", f"a.fn{j}") for i in range(12) for j in range(12) if i != j]
        z_nodes = [("z.iso1", "z.py"), ("z.iso2", "z.py"), ("z.iso3", "z.py")]
        z_edges = [("z.iso1", "z.iso2"), ("z.iso2", "z.iso3"), ("z.iso3", "z.iso1")]
        graph = _build_graph(a_nodes + z_nodes, a_edges + z_edges)
        snap = _snapshot({"python": {1: ClusterSnapshotEntry(members={f"a.fn{i}" for i in range(12)}, files={"a.py"})}})

        delta = compute_cluster_delta(snap, _build_static(graph))

        ld = delta.by_language["python"]
        self.assertFalse(ld.fallback_used)
        self.assertEqual(len(ld.new_cluster_ids), 1)
        self.assertEqual(ld.changed_cluster_ids, set())
        new_cid = next(iter(ld.new_cluster_ids))
        self.assertTrue({"z.iso1", "z.iso2", "z.iso3"}.issubset(ld.cluster_results.clusters[new_cid]))

    def test_removed_node_drops_empty_cluster(self) -> None:
        graph = _build_graph([("a.foo", "a.py"), ("a.bar", "a.py")], [("a.foo", "a.bar")])
        snap = _snapshot(
            {
                "python": {
                    1: ClusterSnapshotEntry(members={"a.foo", "a.bar"}),
                    2: ClusterSnapshotEntry(members={"b.baz"}),
                }
            }
        )

        delta = compute_cluster_delta(snap, _build_static(graph))

        ld = delta.by_language["python"]
        self.assertEqual(ld.dropped_cluster_ids, {2})
        self.assertNotIn(2, ld.cluster_results.clusters)
        self.assertEqual(ld.new_cluster_ids, set())

    def test_added_node_with_no_neighbors_uses_file_overlap(self) -> None:
        graph = _build_graph(
            [("a.foo", "a.py"), ("a.bar", "a.py"), ("a.new", "a.py")],
            [("a.foo", "a.bar")],
        )
        snap = _snapshot({"python": {1: ClusterSnapshotEntry(members={"a.foo", "a.bar"}, files={"a.py"})}})

        delta = compute_cluster_delta(snap, _build_static(graph))

        ld = delta.by_language["python"]
        self.assertEqual(ld.new_cluster_ids, set())
        self.assertEqual(ld.changed_cluster_ids, {1})
        self.assertIn("a.new", ld.cluster_results.clusters[1])


class TestThresholdFallback(unittest.TestCase):
    def test_huge_diff_triggers_flavor_a(self) -> None:
        node_specs = [(f"a.new{i}", "a.py") for i in range(9)] + [("kept", "a.py")]
        graph = _build_graph(node_specs, [])
        snap = _snapshot({"python": {1: ClusterSnapshotEntry(members={f"old{i}" for i in range(9)} | {"kept"})}})

        delta = compute_cluster_delta(snap, _build_static(graph))

        ld = delta.by_language["python"]
        self.assertTrue(ld.fallback_used)


class TestFlavorAMatching(unittest.TestCase):
    def test_jaccard_above_threshold_matches_old_id(self) -> None:
        old = {1: ClusterSnapshotEntry(members={"a", "b", "c"})}
        new_clusters = {7: {"a", "b", "c"}, 8: {"x"}}

        remap, matched = _match_by_jaccard(old, new_clusters)
        self.assertEqual(remap, {1: 7})
        self.assertEqual(matched, {1})

    def test_jaccard_below_threshold_does_not_match(self) -> None:
        old = {1: ClusterSnapshotEntry(members={"a", "b", "c", "d"})}
        new_clusters = {7: {"x", "y", "z", "a"}}  # Jaccard = 1/7 approx 0.14, below 0.5

        remap, matched = _match_by_jaccard(old, new_clusters)
        self.assertEqual(remap, {})
        self.assertEqual(matched, set())

    def test_jaccard_helper(self) -> None:
        self.assertEqual(_jaccard(set(), set()), 0.0)
        self.assertEqual(_jaccard({"a"}, {"a"}), 1.0)
        self.assertAlmostEqual(_jaccard({"a", "b"}, {"a", "c"}), 1 / 3)


class TestClusterDeltaAccessors(unittest.TestCase):
    def test_all_affected_and_dropped_aggregate_across_languages(self) -> None:
        delta = ClusterDelta(
            by_language={
                "python": LanguageDelta(
                    language="python",
                    cluster_results=ClusterResult(),
                    new_cluster_ids={1},
                    changed_cluster_ids={2},
                    dropped_cluster_ids={3},
                ),
                "go": LanguageDelta(
                    language="go",
                    cluster_results=ClusterResult(),
                    new_cluster_ids={10},
                    dropped_cluster_ids={11},
                ),
            }
        )
        self.assertEqual(delta.all_affected_cluster_ids(), {1, 2, 10})
        self.assertEqual(delta.all_dropped_cluster_ids(), {3, 11})

    def test_constants_are_documented(self) -> None:
        self.assertTrue(0 < FULL_RECLUSTER_THRESHOLD < 1)
        self.assertTrue(0 < JACCARD_MATCH_THRESHOLD <= 1)


class TestSnapshotIntegration(unittest.TestCase):
    def test_round_trip_via_snapshot_from_cluster_results(self) -> None:
        graph = _build_graph([("a.foo", "a.py"), ("a.bar", "a.py")], [("a.foo", "a.bar")])
        cluster_results = {"python": ClusterResult(clusters={1: {"a.foo", "a.bar"}})}
        snap = snapshot_from_cluster_results(cluster_results)

        delta = compute_cluster_delta(snap, _build_static(graph))

        self.assertFalse(delta.has_changes)


class TestInternalHelpersSmoke(unittest.TestCase):
    def test_flavor_b_handles_empty_universe(self) -> None:
        graph = _build_graph([], [])
        ld = _flavor_b_iterative("python", graph.to_networkx(), {}, set(), set())
        self.assertFalse(ld.affected_cluster_ids)

    def test_flavor_a_returns_fallback_used_true(self) -> None:
        # ``_flavor_a_fallback`` now takes a precomputed ClusterResult; build
        # one from the live graph the same way ``compute_cluster_delta`` does.
        graph = _build_graph([("a.foo", "a.py"), ("a.bar", "a.py")], [("a.foo", "a.bar")])
        sa = _build_static(graph)
        from static_analyzer.cluster_helpers import build_all_cluster_results

        fresh = build_all_cluster_results(sa).get("python", ClusterResult())
        ld = _flavor_a_fallback("python", fresh, {})
        self.assertTrue(ld.fallback_used)


if __name__ == "__main__":
    unittest.main()
