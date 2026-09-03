import unittest

from static_analyzer.cfg import CallGraph
from static_analyzer.clustering.service import file_leaf_clusters
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
