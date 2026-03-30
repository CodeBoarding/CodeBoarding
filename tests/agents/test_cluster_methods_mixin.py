import unittest
from pathlib import Path
from unittest.mock import MagicMock

import networkx as nx

from agents.cluster_methods_mixin import ClusterMethodsMixin
from agents.agent_responses import AnalysisInsights, ClusterAnalysis, ClustersComponent, Component, SourceCodeReference
from static_analyzer.graph import CallGraph, ClusterResult
from static_analyzer.constants import NodeType
from static_analyzer.node import Node


class MockMixin(ClusterMethodsMixin):
    """Concrete implementation for testing the mixin."""

    def __init__(self, repo_dir: Path, static_analysis: MagicMock):
        self.repo_dir = repo_dir
        self.static_analysis = static_analysis


class TestClusterResult(unittest.TestCase):
    """Test the ClusterResult dataclass from graph.py"""

    def test_get_cluster_ids(self):
        result = ClusterResult(
            clusters={1: {"a", "b"}, 2: {"c"}, 3: {"d", "e", "f"}},
            file_to_clusters={},
            cluster_to_files={},
            strategy="test",
        )
        self.assertEqual(result.get_cluster_ids(), {1, 2, 3})

    def test_get_files_for_cluster(self):
        result = ClusterResult(
            clusters={1: {"a"}},
            file_to_clusters={},
            cluster_to_files={1: {"/test/a.py", "/test/b.py"}, 2: {"/test/c.py"}},
            strategy="test",
        )
        self.assertEqual(result.get_files_for_cluster(1), {"/test/a.py", "/test/b.py"})
        self.assertEqual(result.get_files_for_cluster(2), {"/test/c.py"})
        self.assertEqual(result.get_files_for_cluster(99), set())

    def test_get_clusters_for_file(self):
        result = ClusterResult(
            clusters={1: {"a"}},
            file_to_clusters={"/test/a.py": {1, 2}, "/test/b.py": {3}},
            cluster_to_files={},
            strategy="test",
        )
        self.assertEqual(result.get_clusters_for_file("/test/a.py"), {1, 2})
        self.assertEqual(result.get_clusters_for_file("/test/b.py"), {3})
        self.assertEqual(result.get_clusters_for_file("/nonexistent.py"), set())

    def test_get_nodes_for_cluster(self):
        result = ClusterResult(
            clusters={1: {"node_a", "node_b"}, 2: {"node_c"}},
            file_to_clusters={},
            cluster_to_files={},
            strategy="test",
        )
        self.assertEqual(result.get_nodes_for_cluster(1), {"node_a", "node_b"})
        self.assertEqual(result.get_nodes_for_cluster(99), set())


class TestFindNearestCluster(unittest.TestCase):
    """Tests for _find_nearest_cluster.

    Graph used by most tests (undirected view):

        A -- B -- C -- D
                  |
                  E

    Cluster 1: {A, B}   Cluster 2: {D, E}
    Node C is the orphan we want to assign.
    """

    def _make_call_graph(self) -> CallGraph:
        """Build a small CallGraph: A->B->C->D, C->E."""
        cfg = CallGraph(language="python")
        for name in ("A", "B", "C", "D", "E"):
            cfg.add_node(Node(name, NodeType.FUNCTION, "/src/mod.py", 1, 10))
        cfg.add_edge("A", "B")
        cfg.add_edge("B", "C")
        cfg.add_edge("C", "D")
        cfg.add_edge("C", "E")
        return cfg

    def _make_cluster_result(self) -> ClusterResult:
        return ClusterResult(
            clusters={1: {"A", "B"}, 2: {"D", "E"}},
            file_to_clusters={},
            cluster_to_files={},
            strategy="test",
        )

    def _make_mixin(self, cfg: CallGraph) -> MockMixin:
        static = MagicMock()
        static.get_cfg.return_value = cfg
        return MockMixin(repo_dir=Path("/repo"), static_analysis=static)

    def test_finds_nearest_cluster_by_graph_distance(self):
        """C is 1 hop from both clusters; cluster 2 members D,E are direct neighbours."""
        cfg = self._make_call_graph()
        cr = self._make_cluster_result()
        cluster_results = {"python": cr}
        mixin = self._make_mixin(cfg)

        undirected_graphs = mixin._build_undirected_graphs(cluster_results)
        # C is distance-1 from D (cluster 2) and distance-1 from B (cluster 1).
        # Both clusters have a member at distance 1, so the first one found wins
        # (deterministic dict order).
        result = mixin._find_nearest_cluster("C", cluster_results, undirected_graphs)
        self.assertIn(result, {1, 2})

    def test_returns_none_for_disconnected_node(self):
        """A node not in any graph returns None."""
        cfg = self._make_call_graph()
        # Add an isolated node
        cfg.add_node(Node("Z", NodeType.FUNCTION, "/src/other.py", 1, 5))
        cr = self._make_cluster_result()
        cluster_results = {"python": cr}
        mixin = self._make_mixin(cfg)

        undirected_graphs = mixin._build_undirected_graphs(cluster_results)
        result = mixin._find_nearest_cluster("Z", cluster_results, undirected_graphs)
        self.assertIsNone(result)

    def test_returns_none_when_node_not_in_graph(self):
        """A node name absent from the graph entirely returns None."""
        cfg = self._make_call_graph()
        cr = self._make_cluster_result()
        cluster_results = {"python": cr}
        mixin = self._make_mixin(cfg)

        undirected_graphs = mixin._build_undirected_graphs(cluster_results)
        result = mixin._find_nearest_cluster("NONEXISTENT", cluster_results, undirected_graphs)
        self.assertIsNone(result)

    def test_node_inside_cluster_returns_own_cluster(self):
        """A node that is itself a cluster member should return its own cluster (distance 0)."""
        cfg = self._make_call_graph()
        cr = self._make_cluster_result()
        cluster_results = {"python": cr}
        mixin = self._make_mixin(cfg)

        undirected_graphs = mixin._build_undirected_graphs(cluster_results)
        result = mixin._find_nearest_cluster("A", cluster_results, undirected_graphs)
        self.assertEqual(result, 1)

    def test_prefers_closer_cluster(self):
        """When distances differ, the closer cluster wins.

        Graph: X -> Y -> Z    Cluster 10: {X}, Cluster 20: {Z}
        Y is 1 hop from both — tie. But if we add W -> X so X is farther,
        and test from W: W is distance-1 from X (cluster 10), distance-3 from Z (cluster 20).
        """
        cfg = CallGraph(language="python")
        for name in ("W", "X", "Y", "Z"):
            cfg.add_node(Node(name, NodeType.FUNCTION, "/src/mod.py", 1, 10))
        cfg.add_edge("W", "X")
        cfg.add_edge("X", "Y")
        cfg.add_edge("Y", "Z")

        cr = ClusterResult(
            clusters={10: {"X"}, 20: {"Z"}},
            file_to_clusters={},
            cluster_to_files={},
            strategy="test",
        )
        cluster_results = {"python": cr}
        mixin = self._make_mixin(cfg)

        undirected_graphs = mixin._build_undirected_graphs(cluster_results)
        result = mixin._find_nearest_cluster("W", cluster_results, undirected_graphs)
        self.assertEqual(result, 10)


class TestDeterministicClusterRepairs(unittest.TestCase):
    def test_build_cluster_string_skips_languages_without_cluster_results(self):
        static = MagicMock()
        python_cfg = MagicMock()
        python_cfg.to_cluster_string.return_value = "Cluster 1: A"
        static.get_cfg.return_value = python_cfg
        mixin = MockMixin(repo_dir=Path("/repo"), static_analysis=static)

        cluster_str = mixin._build_cluster_string(
            programming_langs=["python", "javascript"],
            cluster_results={
                "python": ClusterResult(
                    clusters={1: {"A"}},
                    file_to_clusters={},
                    cluster_to_files={},
                    strategy="test",
                )
            },
        )

        self.assertIn("Python - Clusters", cluster_str)
        self.assertNotIn("Javascript - Clusters", cluster_str)
        static.get_cfg.assert_called_once_with("python")

    def test_auto_assign_missing_clusters_prefers_strongest_connected_group(self):
        static = MagicMock()
        cfg = CallGraph(language="python")
        for name in ("A", "B", "C"):
            cfg.add_node(Node(name, NodeType.FUNCTION, "/src/mod.py", 1, 10))
        cfg.add_edge("C", "A")
        static.get_cfg.return_value = cfg
        mixin = MockMixin(repo_dir=Path("/repo"), static_analysis=static)

        cluster_results = {
            "python": ClusterResult(
                clusters={1: {"A"}, 2: {"B"}, 3: {"C"}},
                file_to_clusters={},
                cluster_to_files={},
                strategy="test",
            )
        }
        cluster_analysis = ClusterAnalysis(
            cluster_components=[
                ClustersComponent(name="GroupA", description="desc", cluster_ids=[1]),
                ClustersComponent(name="GroupB", description="desc", cluster_ids=[2]),
            ]
        )

        repaired, unresolved = mixin._auto_assign_missing_clusters(
            cluster_analysis=cluster_analysis,
            expected_cluster_ids={1, 2, 3},
            cluster_results=cluster_results,
        )

        self.assertEqual(unresolved, set())
        self.assertEqual(repaired.cluster_components[0].cluster_ids, [1, 3])

    def test_populate_key_entities_fills_empty_components(self):
        static = MagicMock()
        cfg = CallGraph(language="python")
        for name in ("A", "B"):
            cfg.add_node(Node(name, NodeType.FUNCTION, "/src/mod.py", 1, 10))
        cfg.add_edge("A", "B")
        cfg.add_edge("A", "B")
        static.get_cfg.return_value = cfg
        static.get_languages.return_value = ["python"]
        mixin = MockMixin(repo_dir=Path("/repo"), static_analysis=static)

        cluster_results = {
            "python": ClusterResult(
                clusters={1: {"A", "B"}},
                file_to_clusters={},
                cluster_to_files={},
                strategy="test",
            )
        }
        analysis = AnalysisInsights(
            description="test",
            components=[
                Component(
                    name="Comp",
                    description="desc",
                    key_entities=[],
                    source_group_names=["GroupA"],
                    source_cluster_ids=[1],
                )
            ],
            components_relations=[],
        )

        mixin._populate_key_entities(analysis, cluster_results)

        self.assertTrue(analysis.components[0].key_entities)
        self.assertEqual(analysis.components[0].key_entities[0].qualified_name, "A")


if __name__ == "__main__":
    unittest.main()
