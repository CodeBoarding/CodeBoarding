import unittest
from unittest.mock import patch

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
        result = cluster_graph(_two_cliques())

        self.assertIsInstance(result, ClusterResult)
        clustered = [n for nodes in result.clusters.values() for n in nodes]
        self.assertEqual(sorted(clustered), sorted(_two_cliques().nodes))
        self.assertEqual(len(clustered), len(set(clustered)), "clusters must be disjoint")

    def test_each_clique_lands_in_one_cluster(self):
        result = cluster_graph(_two_cliques())

        owner = {node: cid for cid, nodes in result.clusters.items() for node in nodes}
        for prefix in ("a", "b"):
            ids = {owner[f"{prefix}{i}"] for i in range(5)}
            self.assertEqual(len(ids), 1, f"clique {prefix} was split across {ids}")

    def test_empty_graph_is_an_empty_leiden_result(self):
        result = cluster_graph(nx.DiGraph())

        self.assertEqual(result.strategy, "leiden")
        self.assertEqual(result.clusters, {})

    def test_file_path_node_attribute_drives_the_file_mappings(self):
        graph = _two_cliques()
        for node in graph.nodes:
            graph.nodes[node]["file_path"] = f"/{node[0]}.py"

        result = cluster_graph(graph)

        self.assertEqual(set(result.file_to_clusters), {"/a.py", "/b.py"})
        for cluster_id, files in result.cluster_to_files.items():
            self.assertTrue(files, f"cluster {cluster_id} has no files")

    def test_nodes_need_no_delimiter_structure(self):
        """Node names are opaque strings; the search must not require qualified-name shape."""
        result = cluster_graph(_two_cliques())

        self.assertEqual(result.strategy, "leiden")
        self.assertTrue(result.clusters)

    def test_disconnected_communities_are_still_returned_by_leiden(self):
        graph = nx.DiGraph()
        for component in range(23):
            graph.add_edge(f"mod{component}.a", f"mod{component}.b")

        result = cluster_graph(graph)

        self.assertEqual(result.strategy, "leiden")
        clustered = {name for members in result.clusters.values() for name in members}
        self.assertEqual(clustered, set(graph.nodes))

    def test_singleton_is_a_leiden_community(self):
        graph = nx.DiGraph()
        graph.add_node("only")

        result = cluster_graph(graph)

        self.assertEqual(result.clusters, {1: {"only"}})

    def test_leiden_failure_is_not_replaced_with_another_algorithm(self):
        with (
            patch("static_analyzer.clustering.engine.find_partition", side_effect=RuntimeError("Leiden failed")),
            self.assertRaisesRegex(RuntimeError, "Leiden failed"),
        ):
            cluster_graph(_two_cliques())


if __name__ == "__main__":
    unittest.main()
