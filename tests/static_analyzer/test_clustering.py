import unittest

import networkx as nx

from static_analyzer.cfg import CallGraph
from static_analyzer.clustering import ClusterCache, ClusterResult, ClusteringService
from static_analyzer.constants import ClusteringConfig
from static_analyzer.leiden_utils import find_partition
from static_analyzer.node import Node


def _chain_graph(node_count: int, files: int = 3) -> CallGraph:
    graph = CallGraph()
    for i in range(node_count):
        graph.add_node(Node(f"module.func{i}", 12, f"/file{i % files}.py", i * 10, i * 10 + 5))
    for i in range(node_count - 1):
        graph.add_edge(f"module.func{i}", f"module.func{i+1}")
    return graph


class TestClusteringService(unittest.TestCase):
    def test_returns_cluster_result(self):
        result = ClusteringService().cluster(_chain_graph(10))

        self.assertIsInstance(result, ClusterResult)
        self.assertIsInstance(result.clusters, dict)
        self.assertIsInstance(result.file_to_clusters, dict)
        self.assertIsInstance(result.cluster_to_files, dict)
        self.assertIsInstance(result.strategy, str)

    def test_clustering_does_not_mutate_the_graph(self):
        """The service is pure — the same graph clusters twice to an equal, freshly-built result."""
        graph = _chain_graph(10)
        nodes_before, edges_before = dict(graph.nodes), list(graph.edges)

        first = ClusteringService().cluster(graph)
        second = ClusteringService().cluster(graph)

        self.assertIsNot(first, second)
        self.assertEqual(first.clusters, second.clusters)
        self.assertEqual(graph.nodes, nodes_before)
        self.assertEqual(graph.edges, edges_before)

    def test_connected_components_fallback_keeps_every_component(self):
        """No member may vanish from the partition just because Leiden scored 0."""
        graph = CallGraph()
        target = ClusteringConfig.DEFAULT_TARGET_CLUSTERS
        for component in range(target + 3):
            for i in range(2):
                graph.add_node(Node(f"mod{component}.func{i}", 12, f"/file{component}.py", i * 10, i * 10 + 5))
            graph.add_edge(f"mod{component}.func0", f"mod{component}.func1")

        result = ClusteringService(min_cluster_size=1).cluster(graph)

        self.assertEqual(result.strategy, "connected_components")
        clustered = {qname for members in result.clusters.values() for qname in members}
        self.assertEqual(clustered, set(graph.nodes))

    def test_empty_graph(self):
        result = ClusteringService().cluster(CallGraph())

        self.assertEqual(result.clusters, {})
        self.assertEqual(result.strategy, "empty")

    def test_builds_file_mappings(self):
        graph = CallGraph()
        for name, path, start in [
            ("module.func1", "/path/a.py", 1),
            ("module.func2", "/path/a.py", 20),
            ("module.func3", "/path/b.py", 1),
            ("module.func4", "/path/b.py", 20),
        ]:
            graph.add_node(Node(name, 12, path, start, start + 9))
        graph.add_edge("module.func1", "module.func2")
        graph.add_edge("module.func3", "module.func4")

        result = ClusteringService().cluster(graph)

        self.assertTrue(len(result.file_to_clusters) > 0 or result.strategy in ("empty", "none"))

    def test_is_deterministic(self):
        first = ClusteringService().cluster(_chain_graph(15))
        second = ClusteringService().cluster(_chain_graph(15))

        self.assertEqual(first.clusters.keys(), second.clusters.keys())
        for cid in first.clusters:
            self.assertEqual(first.clusters[cid], second.clusters[cid])

    def test_subgraph_is_clusterable(self):
        graph = _chain_graph(20, files=4)
        sub_graph = graph.filter_by_files({"/file0.py", "/file1.py"})

        self.assertIsInstance(ClusteringService().cluster(sub_graph), ClusterResult)


class TestClusterCache(unittest.TestCase):
    def test_adopt_sets_the_partition_and_root_lineage(self):
        cache = ClusterCache()
        result = ClusterResult(clusters={1: {"a.foo", "a.bar"}})

        cache.adopt(result)

        self.assertIs(cache.result, result)
        self.assertEqual(cache.method_paths.snapshot_dict(), {"a.foo": {"1"}, "a.bar": {"1"}})

    def test_record_scope_leaves_the_top_level_partition_alone(self):
        """Why: a component's sub-partition must not overwrite what cluster_snapshot reads."""
        cache = ClusterCache()
        top_level = ClusterResult(clusters={1: {"a.foo"}})
        cache.adopt(top_level)

        cache.record_scope(ClusterResult(clusters={2: {"a.foo"}}), "1")

        self.assertIs(cache.result, top_level)
        self.assertEqual(cache.method_paths.snapshot_dict(), {"a.foo": {"1", "1.2"}})

    def test_prune_drops_departed_nodes(self):
        cache = ClusterCache()
        cache.adopt(
            ClusterResult(
                clusters={1: {"a.foo", "b.bar"}},
                cluster_to_files={1: {"a.py", "b.py"}},
                file_to_clusters={"a.py": {1}, "b.py": {1}},
            )
        )
        surviving = {"a.foo": Node("a.foo", 12, "a.py", 1, 5)}

        pruned = cache.prune(surviving)

        self.assertEqual(pruned.result.clusters, {1: {"a.foo"}})
        self.assertEqual(pruned.result.cluster_to_files, {1: {"a.py"}})
        self.assertEqual(pruned.result.file_to_clusters, {"a.py": {1}})
        self.assertEqual(pruned.method_paths.snapshot_dict(), {"a.foo": {"1"}})

    def test_prune_without_a_partition(self):
        """An unclustered language prunes to an empty partition, never to None."""
        self.assertEqual(ClusterCache().prune({}).result.clusters, {})


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
