"""Tests for ``diagram_analysis.cluster_delta`` (hierarchical Infomap, no fallback).

Graphs here are production-shaped: every symbol is CONTAINS-linked to its file
node, because file containment is a first-class clustering signal and a
symbol-only graph would cluster nothing like the real thing.
"""

import copy
import unittest
from pathlib import Path

from diagram_analysis.cluster_delta import (
    ClusterDelta,
    ClusterRef,
    LanguageDelta,
    compute_cluster_delta,
    structural_diff_from_delta,
)
from diagram_analysis.cluster_snapshot import (
    ClusterSnapshot,
    ClusterSnapshotEntry,
    snapshot_from_cluster_results,
    snapshot_from_static_analysis,
)
from repo_utils.change_detector import ChangeSet, FileChange
from static_analyzer.analysis_result import StaticAnalysisResults
from static_analyzer.clustering import ClusterResult
from static_analyzer.constants import Language, NodeType
from static_analyzer.infomap_clustering import HierarchicalInfomapClusterer
from static_analyzer.program_graph import (
    ProgramEdge,
    ProgramEdgeKind,
    ProgramGraph,
    ProgramNode,
    ProgramNodeKind,
    ProgramOccurrence,
    file_node_id,
)
from tests.program_graph_factory import make_symbol


def _build_graph(node_specs: list[tuple[str, str]], edges: list[tuple[str, str]]) -> ProgramGraph:
    """Build a program graph from (qname, file) pairs plus CALL edges.

    Distinct line ranges per qname avoid the location-dedup logic in
    ``ProgramGraph.add_node`` that collapses entries sharing
    ``(file, line_start, line_end, type)``.
    """
    graph = ProgramGraph(language="python")
    for file_path in sorted({file_path for _, file_path in node_specs}):
        graph.add_node(ProgramNode(file_node_id(file_path), ProgramNodeKind.FILE, "python", file_path, file_path))
    for idx, (fqn, file_path) in enumerate(node_specs):
        graph.add_node(make_symbol(fqn, NodeType.FUNCTION, file_path, idx * 10, idx * 10 + 1, language="python"))
        graph.add_edge(ProgramEdge(ProgramEdgeKind.CONTAINS, file_node_id(file_path), fqn))
    for src, dst in edges:
        graph.add_edge(
            ProgramEdge(ProgramEdgeKind.CALL, src, dst, [ProgramOccurrence(graph.nodes[src].file_path, 1, 1)])
        )
    return graph


def _build_static(graph: ProgramGraph) -> StaticAnalysisResults:
    results = StaticAnalysisResults()
    results.add_program_graph(Language.PYTHON, graph)
    return results


def _prior_run(node_specs: list[tuple[str, str]], edges: list[tuple[str, str]]) -> tuple[ProgramGraph, ClusterSnapshot]:
    """Cluster a graph the way a completed prior run would, returning it and its snapshot."""
    graph = _build_graph(node_specs, edges)
    HierarchicalInfomapClusterer().cluster(graph)
    return graph, snapshot_from_static_analysis(_build_static(graph))


def _refresh(baseline: ProgramGraph, node_specs: list[tuple[str, str]], edges: list[tuple[str, str]]) -> ProgramGraph:
    """Rebuild the graph carrying the baseline's lineage, as the incremental pipeline does."""
    graph = _build_graph(node_specs, edges)
    graph.cluster_snapshot = copy.deepcopy(baseline.cluster_snapshot)
    return graph


def _snapshot(by_language: dict[str, dict[int, ClusterSnapshotEntry]]) -> ClusterSnapshot:
    return ClusterSnapshot(by_language=by_language)


BASE_NODES = [("a.foo", "a.py"), ("a.bar", "a.py"), ("b.baz", "b.py"), ("b.qux", "b.py")]
BASE_EDGES = [("a.foo", "a.bar"), ("b.baz", "b.qux")]


class TestClusterDelta(unittest.TestCase):
    def test_no_diff_yields_empty_delta(self) -> None:
        # An unchanged weighted graph short-circuits on the fingerprint, so a
        # matching baseline round-trips to has_changes=False.
        graph, snap = _prior_run(BASE_NODES, BASE_EDGES)

        delta = compute_cluster_delta(snap, _build_static(graph))

        self.assertFalse(delta.has_changes)
        ld = delta.by_language["python"]
        self.assertEqual(ld.new_cluster_ids, set())
        self.assertEqual(ld.changed_cluster_ids, set())
        self.assertEqual(ld.dropped_cluster_ids, set())

    def test_no_change_preserves_partition_byte_for_byte(self) -> None:
        # The partition is a pure function of the weighted graph: re-running on
        # an identical graph must reproduce the prior partition exactly, not
        # merely an equivalent one.
        graph, snap = _prior_run(BASE_NODES, BASE_EDGES)

        delta = compute_cluster_delta(snap, _build_static(graph))

        ld = delta.by_language["python"]
        self.assertFalse(delta.has_changes)
        self.assertEqual(ld.cluster_results.clusters[1], {"a.foo", "a.bar"})
        self.assertEqual(ld.cluster_results.clusters[2], {"b.baz", "b.qux"})

    def test_added_node_routed_to_neighbor_cluster(self) -> None:
        baseline, snap = _prior_run(BASE_NODES, BASE_EDGES)
        graph = _refresh(
            baseline,
            BASE_NODES + [("a.new", "a.py")],
            BASE_EDGES + [("a.new", "a.foo")],
        )

        delta = compute_cluster_delta(snap, _build_static(graph))

        self.assertTrue(delta.has_changes)
        ld = delta.by_language["python"]
        self.assertEqual(ld.new_cluster_ids, set())
        self.assertEqual(ld.changed_cluster_ids, {1})
        self.assertEqual(ld.dropped_cluster_ids, set())
        self.assertIn("a.new", ld.cluster_results.clusters[1])

    def test_added_node_with_no_call_edges_joins_its_file_cluster(self) -> None:
        # Why: an added symbol with no calls is still CONTAINS-linked to its
        # file, and that affinity routes it to the cluster its file already owns
        # instead of stranding it in a fresh one.
        baseline, snap = _prior_run(BASE_NODES, BASE_EDGES)
        graph = _refresh(baseline, BASE_NODES + [("a.new", "a.py")], BASE_EDGES)

        delta = compute_cluster_delta(snap, _build_static(graph))

        ld = delta.by_language["python"]
        self.assertEqual(ld.new_cluster_ids, set())
        self.assertEqual(ld.changed_cluster_ids, {1})
        self.assertIn("a.new", ld.cluster_results.clusters[1])

    def test_isolated_added_nodes_form_new_cluster(self) -> None:
        # A connected community arriving in its own file has no affinity to the
        # existing map, so it takes a fresh id rather than joining one.
        a_nodes = [(f"a.fn{i}", "a.py") for i in range(12)]
        a_edges = [(f"a.fn{i}", f"a.fn{j}") for i in range(12) for j in range(12) if i != j]
        z_nodes = [("z.iso1", "z.py"), ("z.iso2", "z.py"), ("z.iso3", "z.py")]
        z_edges = [("z.iso1", "z.iso2"), ("z.iso2", "z.iso3"), ("z.iso3", "z.iso1")]
        baseline, snap = _prior_run(a_nodes, a_edges)
        graph = _refresh(baseline, a_nodes + z_nodes, a_edges + z_edges)

        delta = compute_cluster_delta(snap, _build_static(graph))

        ld = delta.by_language["python"]
        self.assertEqual(len(ld.new_cluster_ids), 1)
        self.assertEqual(ld.changed_cluster_ids, set())
        new_cid = next(iter(ld.new_cluster_ids))
        self.assertTrue({"z.iso1", "z.iso2", "z.iso3"}.issubset(ld.cluster_results.clusters[new_cid]))

    def test_removed_node_drops_empty_cluster(self) -> None:
        baseline, snap = _prior_run(BASE_NODES, BASE_EDGES)
        graph = _refresh(baseline, [("a.foo", "a.py"), ("a.bar", "a.py")], [("a.foo", "a.bar")])

        delta = compute_cluster_delta(snap, _build_static(graph))

        ld = delta.by_language["python"]
        self.assertEqual(ld.dropped_cluster_ids, {2})
        self.assertNotIn(2, ld.cluster_results.clusters)
        self.assertEqual(ld.new_cluster_ids, set())

    def test_dropped_cluster_id_is_not_recycled_for_an_unrelated_module(self) -> None:
        # Why: ids are issued above every id ever handed out, not merely above the
        # surviving ones. Recycling a deleted cluster's id would make the delta read
        # an unrelated module as *changed* and hand its symbols to the component that
        # owned that id before.
        nodes = [
            ("a.one", "a.py"),
            ("a.two", "a.py"),
            ("b.one", "b.py"),
            ("b.two", "b.py"),
            ("c.one", "c.py"),
            ("c.two", "c.py"),
        ]
        edges = [("a.one", "a.two"), ("b.one", "b.two"), ("c.one", "c.two")]
        baseline, snap = _prior_run(nodes, edges)
        self.assertEqual(set(snap.get_language("python")), {1, 2, 3})

        # b.py is deleted outright and an unrelated d.py arrives in the same run.
        graph = _refresh(
            baseline,
            [
                ("a.one", "a.py"),
                ("a.two", "a.py"),
                ("c.one", "c.py"),
                ("c.two", "c.py"),
                ("d.one", "d.py"),
                ("d.two", "d.py"),
            ],
            [("a.one", "a.two"), ("c.one", "c.two"), ("d.one", "d.two")],
        )

        delta = compute_cluster_delta(snap, _build_static(graph))

        ld = delta.by_language["python"]
        self.assertEqual(ld.dropped_cluster_ids, {2})
        self.assertEqual(ld.new_cluster_ids, {4})
        self.assertEqual(ld.changed_cluster_ids, set())
        self.assertEqual(ld.cluster_results.clusters[4], {"d.one", "d.two"})
        # The untouched modules keep the exact ids they were issued.
        self.assertEqual(ld.cluster_results.clusters[1], {"a.one", "a.two"})
        self.assertEqual(ld.cluster_results.clusters[3], {"c.one", "c.two"})

    def test_empty_graph_yields_no_affected_clusters(self) -> None:
        delta = compute_cluster_delta(_snapshot({"python": {}}), _build_static(_build_graph([], [])))

        self.assertFalse(delta.has_changes)
        self.assertFalse(delta.by_language["python"].affected_cluster_ids)


class TestStructuralClusterDiff(unittest.TestCase):
    def test_classifies_unchanged_and_modified_clusters(self) -> None:
        old_snapshot = _snapshot(
            {
                "python": {
                    1: ClusterSnapshotEntry(members={"a.foo", "a.bar"}, files={"a.py"}),
                    2: ClusterSnapshotEntry(members={"b.baz"}, files={"b.py"}),
                }
            }
        )
        delta = ClusterDelta(
            by_language={
                "python": LanguageDelta(
                    language="python",
                    cluster_results=ClusterResult(
                        clusters={1: {"a.foo", "a.bar", "a.new"}, 2: {"b.baz"}},
                        cluster_to_files={1: {"a.py"}, 2: {"b.py"}},
                    ),
                )
            }
        )

        structural = structural_diff_from_delta(old_snapshot, delta)
        lang = structural.by_language["python"]

        self.assertEqual(len(lang.unchanged), 1)
        self.assertEqual(lang.unchanged[0].old_cluster.cluster_id, 2)
        self.assertEqual(len(lang.modified), 1)
        self.assertEqual(lang.modified[0].old_cluster.cluster_id, 1)
        self.assertEqual(lang.modified[0].added_methods, {"a.new"})
        self.assertEqual(lang.modified[0].removed_methods, set())

    def test_dirty_unchanged_cluster_is_modified(self) -> None:
        old_snapshot = _snapshot(
            {
                "python": {
                    1: ClusterSnapshotEntry(
                        members={"a.foo"},
                        files={"/repo/a.py"},
                        member_files={"a.foo": "/repo/a.py"},
                    ),
                }
            }
        )
        delta = ClusterDelta(
            by_language={
                "python": LanguageDelta(
                    language="python",
                    cluster_results=ClusterResult(
                        clusters={1: {"a.foo"}},
                        cluster_to_files={1: {"/repo/a.py"}},
                    ),
                )
            }
        )
        changes = ChangeSet(files=[FileChange(status_code="M", file_path="a.py")])

        structural = structural_diff_from_delta(old_snapshot, delta, changes=changes, repo_dir=Path("/repo"))
        lang = structural.by_language["python"]

        self.assertEqual(lang.unchanged, [])
        self.assertEqual(len(lang.modified), 1)
        self.assertEqual(lang.modified[0].dirty_files, {"a.py"})
        self.assertEqual(lang.modified[0].added_methods, set())
        self.assertEqual(lang.modified[0].removed_methods, set())

    def test_classifies_new_and_removed_clusters(self) -> None:
        old_snapshot = _snapshot(
            {
                "python": {
                    1: ClusterSnapshotEntry(members={"a.foo"}),
                    2: ClusterSnapshotEntry(members={"b.gone"}),
                }
            }
        )
        delta = ClusterDelta(
            by_language={
                "python": LanguageDelta(
                    language="python",
                    cluster_results=ClusterResult(
                        clusters={1: {"a.foo"}, 3: {"c.new"}},
                        cluster_to_files={1: {"a.py"}, 3: {"c.py"}},
                    ),
                )
            }
        )

        structural = structural_diff_from_delta(old_snapshot, delta)
        lang = structural.by_language["python"]

        self.assertEqual(lang.new, [ClusterRef(language="python", cluster_id=3)])
        self.assertEqual(len(lang.new_details), 1)
        self.assertEqual(lang.new_details[0].new_cluster, ClusterRef(language="python", cluster_id=3))
        self.assertEqual(lang.new_details[0].added_methods, {"c.new"})
        self.assertEqual(lang.removed, [ClusterRef(language="python", cluster_id=2)])

    def test_split_or_merge_overlap_becomes_reshaped(self) -> None:
        old_snapshot = _snapshot(
            {
                "python": {
                    1: ClusterSnapshotEntry(members={"a.one", "a.two"}),
                    2: ClusterSnapshotEntry(members={"b.one"}),
                }
            }
        )
        delta = ClusterDelta(
            by_language={
                "python": LanguageDelta(
                    language="python",
                    cluster_results=ClusterResult(clusters={10: {"a.one", "b.one"}, 11: {"a.two"}}),
                )
            }
        )

        structural = structural_diff_from_delta(old_snapshot, delta)
        lang = structural.by_language["python"]

        self.assertEqual(lang.modified, [])
        self.assertEqual(len(lang.reshaped), 1)
        reshape = lang.reshaped[0]
        self.assertEqual({ref.cluster_id for ref in reshape.old_clusters}, {1, 2})
        self.assertEqual({ref.cluster_id for ref in reshape.new_clusters}, {10, 11})
        self.assertEqual(
            reshape.overlap_counts[
                (ClusterRef(language="python", cluster_id=1), ClusterRef(language="python", cluster_id=10))
            ],
            1,
        )


class TestSnapshotIntegration(unittest.TestCase):
    def test_round_trip_via_snapshot_from_cluster_results(self) -> None:
        graph, _ = _prior_run([("a.foo", "a.py"), ("a.bar", "a.py")], [("a.foo", "a.bar")])
        snap = snapshot_from_cluster_results({"python": ClusterResult(clusters={1: {"a.foo", "a.bar"}})})

        delta = compute_cluster_delta(snap, _build_static(graph))

        self.assertFalse(delta.has_changes)


class TestDiffScoping(unittest.TestCase):
    """Four-quadrant reporting on (in_prior_analysis, in_source_diff)."""

    def _changeset(self, paths: list[str]) -> ChangeSet:
        return ChangeSet(files=[FileChange(status_code="M", file_path=p) for p in paths])

    def test_drift_in_unchanged_file_is_reported_not_gated(self) -> None:
        # A new qname in a file absent from the diff still reaches the delta:
        # the partition is correct for the graph it was given, so the mismatch
        # between static analysis and change detection is logged, not gated.
        baseline, snap = _prior_run(BASE_NODES, BASE_EDGES)
        graph = _refresh(
            baseline,
            BASE_NODES + [("b.drift", "b.py")],
            BASE_EDGES + [("b.drift", "b.baz")],
        )

        with self.assertLogs("diagram_analysis.cluster_delta", level="WARNING") as captured:
            delta = compute_cluster_delta(snap, _build_static(graph), changes=self._changeset(["a.py"]))

        ld = delta.by_language["python"]
        self.assertIn(2, ld.changed_cluster_ids)
        self.assertIn("b.drift", ld.cluster_results.clusters[2])
        joined = "\n".join(captured.output)
        self.assertIn("outside the source diff", joined)
        self.assertIn("b.drift", joined)

    def test_real_addition_in_changed_file_flows_through(self) -> None:
        baseline, snap = _prior_run(BASE_NODES, BASE_EDGES)
        graph = _refresh(
            baseline,
            BASE_NODES + [("a.new", "a.py")],
            BASE_EDGES + [("a.new", "a.foo")],
        )

        delta = compute_cluster_delta(snap, _build_static(graph), changes=self._changeset(["a.py"]))

        ld = delta.by_language["python"]
        self.assertTrue(delta.has_changes, "real new qname in diff'd file must produce delta")
        self.assertEqual(ld.changed_cluster_ids, {1}, "a.new should be routed into cluster 1")

    def test_inconsistent_removal_is_kept_and_logged(self) -> None:
        # Tracked qname disappears without its file being in the diff — the
        # (in-analysis, not-in-diff) inconsistent quadrant. The qname must still
        # be peeled off its cluster AND a WARNING must record the inconsistency.
        baseline, _ = _prior_run(BASE_NODES, BASE_EDGES)
        snap = _snapshot(
            {
                "python": {
                    1: ClusterSnapshotEntry(
                        members={"a.foo", "a.bar"},
                        member_files={"a.foo": "a.py", "a.bar": "a.py"},
                    ),
                    2: ClusterSnapshotEntry(
                        members={"b.baz", "b.qux", "b.lost"},
                        member_files={"b.baz": "b.py", "b.qux": "b.py", "b.lost": "b.py"},
                    ),
                }
            }
        )
        graph = _refresh(baseline, BASE_NODES, BASE_EDGES)

        with self.assertLogs("diagram_analysis.cluster_delta", level="WARNING") as captured:
            delta = compute_cluster_delta(snap, _build_static(graph), changes=self._changeset(["a.py"]))

        ld = delta.by_language["python"]
        self.assertIn(2, ld.changed_cluster_ids)
        joined = "\n".join(captured.output)
        self.assertIn("outside the source diff", joined)
        self.assertIn("b.lost", joined)

    def test_changes_none_preserves_unscoped_behavior(self) -> None:
        # Callers without a diff source (e.g. the GitHub Action) get unscoped
        # behavior: every live qname absent from the prior snapshot counts as added.
        baseline, snap = _prior_run([("a.foo", "a.py"), ("a.bar", "a.py")], [("a.foo", "a.bar")])
        graph = _refresh(
            baseline,
            [("a.foo", "a.py"), ("a.bar", "a.py"), ("a.new", "a.py")],
            [("a.foo", "a.bar"), ("a.new", "a.foo")],
        )

        delta = compute_cluster_delta(snap, _build_static(graph), changes=None)

        ld = delta.by_language["python"]
        self.assertTrue(delta.has_changes, "an added qname must surface as some kind of change")
        all_qnames = {q for members in ld.cluster_results.clusters.values() for q in members}
        self.assertIn("a.new", all_qnames, "a.new must appear in some cluster")


if __name__ == "__main__":
    unittest.main()
