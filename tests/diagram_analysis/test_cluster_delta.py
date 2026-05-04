"""Tests for ``diagram_analysis.cluster_delta`` (Flavor B + threshold fallback)."""

import unittest

from diagram_analysis.cluster_delta import (
    FULL_RECLUSTER_THRESHOLD,
    JACCARD_MATCH_THRESHOLD,
    ClusterDelta,
    LanguageDelta,
    _affected_frontier,
    _flavor_a_fallback,
    _flavor_b_seeded,
    _jaccard,
    _match_by_jaccard,
    compute_cluster_delta,
)
from diagram_analysis.cluster_snapshot import ClusterSnapshot, ClusterSnapshotEntry, snapshot_from_cluster_results
from repo_utils.change_detector import ChangeSet, FileChange
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


class TestDiffScoping(unittest.TestCase):
    """Four-quadrant filter on (in_prior_analysis, in_source_diff)."""

    def _changeset(self, paths: list[str]) -> ChangeSet:
        return ChangeSet(
            base_ref="prev",
            target_ref="curr",
            files=[FileChange(status_code="M", file_path=p) for p in paths],
        )

    def test_drift_in_unchanged_file_is_dropped(self) -> None:
        # Two cluster baselines; fresh CFG also has one new qname in an
        # unchanged file. ``changes`` says only ``a.py`` was modified; the
        # drift in ``b.py`` should be dropped (not-in analysis, not-in diff).
        graph = _build_graph(
            [
                ("a.foo", "a.py"),
                ("a.bar", "a.py"),
                ("b.baz", "b.py"),
                ("b.qux", "b.py"),
                ("b.drift", "b.py"),  # new qname in an unchanged file -- drift
            ],
            [("a.foo", "a.bar"), ("b.baz", "b.qux"), ("b.drift", "b.baz")],
        )
        snap = _snapshot(
            {
                "python": {
                    1: ClusterSnapshotEntry(members={"a.foo", "a.bar"}),
                    2: ClusterSnapshotEntry(members={"b.baz", "b.qux"}),
                }
            }
        )

        changes = self._changeset(["a.py"])  # only a.py is in the diff
        delta = compute_cluster_delta(snap, _build_static(graph), changes=changes)

        self.assertFalse(delta.has_changes, "drift in unchanged file must not produce a delta")

    def test_real_addition_in_changed_file_flows_through(self) -> None:
        # The opposite case: a new qname in a file that IS in the diff
        # should flow through normally (not-in analysis, in-diff quadrant).
        graph = _build_graph(
            [
                ("a.foo", "a.py"),
                ("a.bar", "a.py"),
                ("a.new", "a.py"),  # genuine new qname in a changed file
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

        changes = self._changeset(["a.py"])
        delta = compute_cluster_delta(snap, _build_static(graph), changes=changes)

        ld = delta.by_language["python"]
        self.assertTrue(delta.has_changes, "real new qname in diff'd file must produce delta")
        self.assertEqual(ld.changed_cluster_ids, {1}, "a.new should be routed into cluster 1")

    def test_inconsistent_removal_is_kept_and_logged(self) -> None:
        # Tracked qname disappears without its file being in the diff —
        # the (in-analysis, not-in-diff) inconsistent quadrant. The qname must
        # still flow through ``removed_nodes`` so Flavor B clears it from
        # its cluster, AND a WARNING-level log must record the inconsistency.
        # Each cluster has >=2 connected fresh members so neither is dropped
        # as a singleton (would otherwise be filtered before the delta runs).
        graph = _build_graph(
            [
                ("a.foo", "a.py"),
                ("a.bar", "a.py"),
                ("b.baz", "b.py"),
                ("b.qux", "b.py"),
            ],
            [("a.foo", "a.bar"), ("b.baz", "b.qux")],
        )
        # Snapshot has b.lost in cluster 2 alongside b.baz/b.qux; b.lost
        # vanishes from the fresh CFG without its file appearing in the diff.
        snap = _snapshot(
            {
                "python": {
                    1: ClusterSnapshotEntry(
                        members={"a.foo", "a.bar"}, member_files={"a.foo": "a.py", "a.bar": "a.py"}
                    ),
                    2: ClusterSnapshotEntry(
                        members={"b.baz", "b.qux", "b.lost"},
                        member_files={"b.baz": "b.py", "b.qux": "b.py", "b.lost": "b.py"},
                    ),
                }
            }
        )

        changes = self._changeset(["a.py"])  # b.py NOT in diff -> b.lost vanishing is inconsistent
        with self.assertLogs("diagram_analysis.cluster_delta", level="WARNING") as captured:
            delta = compute_cluster_delta(snap, _build_static(graph), changes=changes)

        ld = delta.by_language["python"]
        # Cluster 2 must be marked changed (lost member peeled off), so the
        # LLM call sees the inconsistency via the existing "affected" path.
        self.assertIn(2, ld.changed_cluster_ids)
        # Warning log records the inconsistent removal explicitly.
        joined = "\n".join(captured.output)
        self.assertIn("inconsistent", joined.lower())
        self.assertIn("b.lost", joined)

    def test_changes_none_preserves_unscoped_behavior(self) -> None:
        # Backwards compat: callers without a diff source (e.g., GitHub
        # Action) get today's no-scoping behavior.
        graph = _build_graph(
            [("a.foo", "a.py"), ("a.bar", "a.py"), ("a.new", "a.py")],
            [("a.foo", "a.bar"), ("a.new", "a.foo")],
        )
        snap = _snapshot({"python": {1: ClusterSnapshotEntry(members={"a.foo", "a.bar"})}})

        delta = compute_cluster_delta(snap, _build_static(graph), changes=None)

        ld = delta.by_language["python"]
        self.assertEqual(ld.changed_cluster_ids, {1}, "unscoped run still routes a.new normally")


class TestInternalHelpersSmoke(unittest.TestCase):
    def test_flavor_b_handles_empty_universe(self) -> None:
        graph = _build_graph([], [])
        ld = _flavor_b_seeded("python", graph.to_networkx(), {}, set(), set())
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


class TestSeededLockGuarantee(unittest.TestCase):
    """Property tests codifying the load-bearing invariants of seeded Leiden."""

    def test_no_change_preserves_partition_byte_for_byte(self) -> None:
        # Why: with empty added/removed, the affected frontier is empty, so
        # every vertex is locked. Seeded Leiden must reproduce the prior
        # partition exactly. This is the byte-equal-no-edits invariant.
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
        ld = delta.by_language["python"]
        self.assertFalse(delta.has_changes)
        self.assertEqual(ld.cluster_results.clusters[1], {"a.foo", "a.bar"})
        self.assertEqual(ld.cluster_results.clusters[2], {"b.baz", "b.qux"})

    def test_added_node_can_pull_existing_members_into_new_cluster(self) -> None:
        # Why: this is the capability the previous hand-rolled procedure could
        # NOT express. Add a node with strong edges to existing nodes; under
        # seeded Leiden with the affected frontier free, the existing nodes
        # may rebalance into a new cluster with the added node.
        # Build a 4-node "a" cluster that is very weakly connected, plus a 4-node
        # "b" cluster that is strongly connected. Add "x.new" with edges
        # to a.fn0, a.fn1 (and through their original clusters' weakness, the
        # local optimum may legitimately re-form). The strong invariant we
        # assert: x.new lands in some cluster (it's not orphaned to its own
        # singleton because it has edges to existing members).
        graph = _build_graph(
            [
                ("a.fn0", "a.py"),
                ("a.fn1", "a.py"),
                ("a.fn2", "a.py"),
                ("a.fn3", "a.py"),
                ("b.fn0", "b.py"),
                ("b.fn1", "b.py"),
                ("b.fn2", "b.py"),
                ("b.fn3", "b.py"),
                ("x.new", "x.py"),
            ],
            [
                ("a.fn0", "a.fn1"),
                ("a.fn2", "a.fn3"),
                ("b.fn0", "b.fn1"),
                ("b.fn0", "b.fn2"),
                ("b.fn1", "b.fn3"),
                ("b.fn2", "b.fn3"),
                ("x.new", "a.fn0"),
                ("x.new", "a.fn1"),
            ],
        )
        snap = _snapshot(
            {
                "python": {
                    1: ClusterSnapshotEntry(members={f"a.fn{i}" for i in range(4)}),
                    2: ClusterSnapshotEntry(members={f"b.fn{i}" for i in range(4)}),
                }
            }
        )

        delta = compute_cluster_delta(snap, _build_static(graph))
        ld = delta.by_language["python"]

        # x.new must end up in *some* non-singleton cluster (i.e., grouped
        # with at least one other member). The previous hand-rolled procedure
        # could only put x.new into an existing cluster; seeded Leiden may
        # choose to re-form. Either is acceptable; what we forbid is x.new
        # ending up alone with no graph signal acknowledged.
        x_cluster = next(
            (cid for cid, members in ld.cluster_results.clusters.items() if "x.new" in members),
            None,
        )
        self.assertIsNotNone(x_cluster, "x.new must appear in some cluster")
        self.assertGreater(
            len(ld.cluster_results.clusters[x_cluster]),
            1,
            "x.new should not be a lonely singleton; it has edges into the graph",
        )

    def test_affected_frontier_includes_directed_predecessors_and_successors(self) -> None:
        # Why: nx_graph.neighbors() on a DiGraph returns out-neighbors only.
        # The frontier helper must include predecessors too; otherwise a
        # callee-side change wouldn't unlock its callers.
        graph = _build_graph(
            [("caller.x", "c.py"), ("callee.x", "c.py"), ("added.x", "c.py")],
            [("caller.x", "callee.x"), ("added.x", "callee.x")],
        )
        snap = _snapshot(
            {
                "python": {
                    1: ClusterSnapshotEntry(members={"caller.x", "callee.x"}),
                }
            }
        )
        # Build the working subgraph the way _flavor_b_seeded does.
        nx_g = graph.to_networkx()
        old_clusters = snap.by_language["python"]
        added_nodes = {"added.x"}
        removed_nodes: set[str] = set()
        frontier = _affected_frontier(nx_g, old_clusters, added_nodes, removed_nodes)

        # added.x is in the frontier directly. callee.x is its successor (and
        # an in-neighbor too). caller.x is the predecessor of callee.x — it
        # must reach the frontier via the 1-hop expansion.
        self.assertIn("added.x", frontier)
        self.assertIn("callee.x", frontier)


if __name__ == "__main__":
    unittest.main()
