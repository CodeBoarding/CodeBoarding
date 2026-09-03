import unittest

import networkx as nx

from static_analyzer.cfg import CallGraph
from static_analyzer.clustering import ClusterCache, ClusterResult
from static_analyzer.clustering.service import file_leaf_clusters
from static_analyzer.leiden_utils import find_partition
from static_analyzer.node import Node


def _chain_graph(node_count: int, files: int = 3) -> CallGraph:
    graph = CallGraph()
    for i in range(node_count):
        graph.add_node(Node(f"module.func{i}", 12, f"/file{i % files}.py", i * 10, i * 10 + 5))
    for i in range(node_count - 1):
        graph.add_edge(f"module.func{i}", f"module.func{i+1}")
    return graph


class TestFileLeafClusters(unittest.TestCase):
    def test_one_leaf_per_file_holding_every_node_of_it(self):
        result = file_leaf_clusters(_chain_graph(10, files=3))

        self.assertEqual(result.strategy, "file_leaves")
        self.assertEqual(len(result.clusters), 3)
        clustered = {qname for members in result.clusters.values() for qname in members}
        self.assertEqual(clustered, {f"module.func{i}" for i in range(10)})
        for cluster_id, files in result.cluster_to_files.items():
            self.assertEqual(len(files), 1)
            self.assertEqual(result.file_to_clusters[next(iter(files))], {cluster_id})

    def test_is_deterministic_and_leaves_the_graph_alone(self):
        graph = _chain_graph(15)
        nodes_before, edges_before = dict(graph.nodes), list(graph.edges)

        first = file_leaf_clusters(graph)
        second = file_leaf_clusters(graph)

        self.assertIsNot(first, second)
        self.assertEqual(first.clusters, second.clusters)
        self.assertEqual(graph.nodes, nodes_before)
        self.assertEqual(graph.edges, edges_before)

    def test_empty_graph(self):
        self.assertEqual(file_leaf_clusters(CallGraph()).clusters, {})


class TestClusterCache(unittest.TestCase):
    def test_records_complete_root_lineage(self):
        cache = ClusterCache()
        result = ClusterResult(clusters={1: {"a.foo", "a.bar"}})

        cache.record_scope(result, {"a.orphan"})

        self.assertIs(cache.get_partition(), result)
        self.assertEqual(cache.get_unclustered_members(), {"a.orphan"})

    def test_record_scope_leaves_the_top_level_partition_alone(self):
        cache = ClusterCache()
        top_level = ClusterResult(clusters={1: {"a.foo"}})
        child = ClusterResult(clusters={2: {"a.foo"}})
        cache.record_scope(top_level)

        cache.record_scope(child, {"a.orphan"}, "1")

        self.assertIs(cache.get_partition(), top_level)
        self.assertIs(cache.get_partition("1"), child)
        self.assertEqual(cache.get_unclustered_members("1"), {"a.orphan"})

    def test_select_keeps_only_surviving_nodes(self):
        cache = ClusterCache()
        cache.record_scope(
            ClusterResult(
                clusters={1: {"a.foo", "b.bar"}},
                cluster_to_files={1: {"a.py", "b.py"}},
                file_to_clusters={"a.py": {1}, "b.py": {1}},
            ),
            {"a.orphan", "b.orphan"},
        )
        cache.record_scope(
            ClusterResult(clusters={2: {"a.foo", "b.bar"}}),
            {"a.foo", "b.orphan"},
            "1",
        )
        surviving = {"a.foo": Node("a.foo", 12, "a.py", 1, 5)}

        kept = cache.select(surviving)

        self.assertEqual(kept.get_partition().clusters, {1: {"a.foo"}})
        self.assertEqual(kept.get_partition().cluster_to_files, {1: {"a.py"}})
        self.assertEqual(kept.get_partition().file_to_clusters, {"a.py": {1}})
        self.assertEqual(kept.get_partition("1").clusters, {2: {"a.foo"}})
        self.assertEqual(kept.get_unclustered_members(), set())
        self.assertEqual(kept.get_unclustered_members("1"), {"a.foo"})

    def test_select_without_a_partition(self):
        """An unclustered language selects to an empty partition, never to None."""
        self.assertEqual(ClusterCache().select({}).get_partition().clusters, {})


class TestFindPartitionDeterminism(unittest.TestCase):
    """Property test: same input + same seed -> byte-equal output.

    Why: determinism is the contract every downstream piece relies on — cluster
    IDs persisted in analysis.json must reproduce on subsequent runs.
    """

    def test_find_partition_is_deterministic(self):
        graph = nx.karate_club_graph()

        a: list[set[int]] = find_partition(graph, seed=42)
        b: list[set[int]] = find_partition(graph, seed=42)

        self.assertEqual(sorted(sorted(c) for c in a), sorted(sorted(c) for c in b))
