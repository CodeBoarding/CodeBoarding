import unittest

import networkx as nx

from static_analyzer.clustering.engine import cluster_graph
from static_analyzer.clustering.models import ClusterResult


def _two_cliques() -> nx.DiGraph:
    """Two 5-node cliques joined by a single edge — an unambiguous two-community split."""
    graph = nx.DiGraph()
    for prefix in ("a", "b"):
        members = [f"{prefix}{i}" for i in range(5)]
        for src in members:
            for dst in members:
                if src != dst:
                    graph.add_edge(src, dst)
    graph.add_edge("a0", "b0")
    return graph


class TestClusterGraph(unittest.TestCase):
    """cluster_graph is driven with bare nx.DiGraphs here — no CallGraph, no LSP."""

    def test_clusters_a_hand_built_digraph(self):
        result = cluster_graph(_two_cliques(), delimiter=".")

        self.assertIsInstance(result, ClusterResult)
        clustered = [n for nodes in result.clusters.values() for n in nodes]
        self.assertEqual(sorted(clustered), sorted(_two_cliques().nodes))
        self.assertEqual(len(clustered), len(set(clustered)), "clusters must be disjoint")

    def test_each_clique_lands_in_one_cluster(self):
        result = cluster_graph(_two_cliques(), delimiter=".")

        owner = {node: cid for cid, nodes in result.clusters.items() for node in nodes}
        for prefix in ("a", "b"):
            ids = {owner[f"{prefix}{i}"] for i in range(5)}
            self.assertEqual(len(ids), 1, f"clique {prefix} was split across {ids}")

    def test_empty_graph_reports_the_empty_strategy(self):
        result = cluster_graph(nx.DiGraph(), delimiter=".")

        self.assertEqual(result.strategy, "empty")
        self.assertEqual(result.clusters, {})

    def test_file_path_node_attribute_drives_the_file_mappings(self):
        graph = _two_cliques()
        for node in graph.nodes:
            graph.nodes[node]["file_path"] = f"/{node[0]}.py"

        result = cluster_graph(graph, delimiter=".")

        self.assertEqual(set(result.file_to_clusters), {"/a.py", "/b.py"})
        for cluster_id, files in result.cluster_to_files.items():
            self.assertTrue(files, f"cluster {cluster_id} has no files")

    def test_nodes_need_no_delimiter_structure(self):
        """Node names are opaque strings; the search must not require qualified-name shape."""
        result = cluster_graph(_two_cliques(), delimiter="::")

        self.assertNotEqual(result.strategy, "empty")
        self.assertTrue(result.clusters)

    def test_falls_back_to_connected_components_when_nothing_scores(self):
        """Leiden is the only algorithm, and a minimum of 6 leaves it nothing to return at any level."""
        result = cluster_graph(_two_cliques(), delimiter=".", min_cluster_size=6)

        self.assertEqual(result.strategy, "connected_components")
        self.assertEqual(sorted(result.clusters[1]), sorted(_two_cliques().nodes))


if __name__ == "__main__":
    unittest.main()
