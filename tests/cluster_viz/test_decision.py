"""Tests for ``cluster_viz.decision``: the replay must reproduce the real grouping.

The trace is only worth reading if it is the same decision the pipeline made, so
every case checks the replayed groups against ``supercluster_by_modularity_peak``.
"""

import unittest

import networkx as nx

from cluster_viz.decision import replay_grouping
from static_analyzer.graph import ClusterResult


def _three_communities() -> tuple[ClusterResult, nx.DiGraph]:
    """Nine clusters in three tight communities, plus one cluster nothing calls."""
    clusters: dict[int, set[str]] = {}
    cluster_to_files: dict[int, set[str]] = {}
    graph = nx.DiGraph()
    for community in range(3):
        for index in range(3):
            cluster_id = community * 3 + index
            members = {f"pkg{community}.mod{index}.fn{n}" for n in range(3)}
            clusters[cluster_id] = members
            cluster_to_files[cluster_id] = {f"pkg{community}/mod{index}.py"}
            graph.add_nodes_from(members)
    for community in range(3):
        members = [f"pkg{community}.mod{index}.fn{n}" for index in range(3) for n in range(3)]
        for source in members:
            for target in members:
                if source != target:
                    graph.add_edge(source, target)
    # One thin strand between community 0 and 1 so the meta-graph is connected.
    graph.add_edge("pkg0.mod0.fn0", "pkg1.mod0.fn0")

    clusters[9] = {"pkg0.orphan.fn0"}
    cluster_to_files[9] = {"pkg0/orphan.py"}
    graph.add_node("pkg0.orphan.fn0")

    result = ClusterResult(clusters=clusters, cluster_to_files=cluster_to_files, strategy="test")
    return result, graph


class TestReplayGrouping(unittest.TestCase):
    def setUp(self):
        self.cluster_result, self.graph = _three_communities()
        self.decision, self.meta_graph = replay_grouping(self.cluster_result, self.graph, low=3, high=8)

    def test_replay_reproduces_the_pipeline_grouping(self):
        self.assertTrue(self.decision.matches_pipeline, self.decision.note)

    def test_groups_are_a_disjoint_cover_of_every_leaf_cluster(self):
        assigned = [cid for group in self.decision.groups for cid in group]
        self.assertEqual(sorted(assigned), sorted(self.cluster_result.clusters))

    def test_sweep_records_one_winner_and_marks_the_target_range(self):
        chosen = [entry for entry in self.decision.sweep if entry.chosen]
        self.assertEqual(len(chosen), 1)
        self.assertTrue(chosen[0].in_range)
        self.assertEqual(self.decision.low, 3)

    def test_disconnected_cluster_is_absorbed_with_a_recorded_reason(self):
        absorbed = {item.cluster_id: item for item in self.decision.absorptions}
        self.assertIn(9, absorbed)
        self.assertEqual(absorbed[9].reason, "package")
        self.assertEqual(absorbed[9].package_affinity, 1)

    def test_meta_graph_carries_one_node_per_leaf_cluster(self):
        self.assertEqual(set(self.meta_graph.nodes), set(self.cluster_result.clusters))


class TestDegenerateScopes(unittest.TestCase):
    def test_fewer_clusters_than_the_floor_become_one_component_each(self):
        clusters = {0: {"a.fn"}, 1: {"b.fn"}}
        graph = nx.DiGraph([("a.fn", "b.fn")])
        decision, _ = replay_grouping(ClusterResult(clusters=clusters), graph, low=3, high=8)

        self.assertEqual(sorted(decision.groups), [[0], [1]])
        self.assertIn("floor", decision.note)
        self.assertEqual(decision.sweep, [])

    def test_unconnected_clusters_are_still_grouped_to_the_floor(self):
        """An edgeless meta-graph does not stop the pipeline — it promotes and absorbs anyway."""
        clusters = {index: {f"m{index}.fn"} for index in range(5)}
        graph = nx.DiGraph()
        graph.add_nodes_from(f"m{index}.fn" for index in range(5))
        decision, _ = replay_grouping(ClusterResult(clusters=clusters), graph, low=3, high=8)

        self.assertTrue(decision.matches_pipeline, decision.note)
        self.assertEqual(len(decision.groups), 3)
        self.assertEqual(len(decision.promoted), 3)
        self.assertEqual(decision.sweep, [])
        self.assertEqual(decision.modularity, 0.0)
        self.assertIn("no edges between the leaf clusters", decision.note)

    def test_empty_scope_reports_no_groups(self):
        decision, _ = replay_grouping(ClusterResult(), nx.DiGraph(), low=3, high=8)

        self.assertEqual(decision.groups, [])
        self.assertEqual(decision.leaf_clusters, 0)
