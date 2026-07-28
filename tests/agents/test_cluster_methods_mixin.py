import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import networkx as nx

from agents.cluster_ids import CodeBoardingClusterIds
from agents.cluster_methods_mixin import ClusterMethodsMixin
from agents.agent_responses import (
    AnalysisInsights,
    Component,
    SourceCodeReference,
)
from agents.file_index_models import FileMethodGroup, MethodEntry
from static_analyzer.graph import CallGraph, ClusterResult
from static_analyzer.constants import Language, NodeType
from static_analyzer.node import Node


class MockMixin(ClusterMethodsMixin):
    """Concrete implementation for testing the mixin."""

    def __init__(self, repo_dir: Path, static_analysis: MagicMock):
        self.repo_dir = repo_dir
        self.static_analysis = static_analysis


class TestBuildScopeCfgString(unittest.TestCase):
    def test_keeps_prompt_examples_bounded_while_counting_all_edges(self):
        cfg = CallGraph(language="python")
        for i in range(12):
            cfg.add_node(Node(f"a.f{i}", NodeType.FUNCTION, "src/a.py", i + 1, i + 1))
            cfg.add_node(Node(f"b.f{i}", NodeType.FUNCTION, "src/b.py", i + 20, i + 20))
            cfg.add_edge(f"a.f{i}", f"b.f{i}")

        static = MagicMock()
        static.get_languages.return_value = ["python"]
        static.get_cfg.return_value = cfg
        static.available_cfgs.return_value = {"python": cfg}
        mixin = MockMixin(repo_dir=Path("/repo"), static_analysis=static)
        analysis = AnalysisInsights(
            description="test",
            components=[
                Component(
                    name="A",
                    description="A",
                    key_entities=[],
                    component_id="1",
                    file_methods=[
                        FileMethodGroup(
                            file_path="src/a.py",
                            methods=[
                                MethodEntry(
                                    qualified_name=f"a.f{i}", start_line=i + 1, end_line=i + 1, node_type="FUNCTION"
                                )
                                for i in range(12)
                            ],
                        )
                    ],
                ),
                Component(
                    name="B",
                    description="B",
                    key_entities=[],
                    component_id="2",
                    file_methods=[
                        FileMethodGroup(
                            file_path="src/b.py",
                            methods=[
                                MethodEntry(
                                    qualified_name=f"b.f{i}", start_line=i + 20, end_line=i + 20, node_type="FUNCTION"
                                )
                                for i in range(12)
                            ],
                        )
                    ],
                ),
            ],
            components_relations=[],
        )

        rendered = mixin.build_scope_cfg_string(analysis)

        self.assertIn("A -> B (12 edges):", rendered)
        self.assertIn("... and 2 more", rendered)
        self.assertEqual(rendered.count("  f"), 10)


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


class TestCodeBoardingClusterIds(unittest.TestCase):
    def test_prefix_for_scope_omits_root_sentinel(self):
        self.assertEqual(CodeBoardingClusterIds.prefix_for_scope("root"), "")
        self.assertEqual(CodeBoardingClusterIds.prefix_for_scope("1.1"), "1.1")

    def test_sort_groups_by_depth_then_uses_natural_order(self):
        self.assertEqual(CodeBoardingClusterIds.sort({"10", "2.1", "3.4", "1", "2"}), ["1", "2", "10", "2.1", "3.4"])

    def test_qualify_local_ids_always_prefixes_non_root_scope(self):
        self.assertEqual(CodeBoardingClusterIds.qualify_local_ids(["1", "2"], "1.1"), ["1.1.1", "1.1.2"])
        self.assertEqual(CodeBoardingClusterIds.qualify_local_ids(["1.1.1"], "1.1"), ["1.1.1.1.1"])


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
        for i, name in enumerate(("A", "B", "C", "D", "E")):
            cfg.add_node(Node(name, NodeType.FUNCTION, "/src/mod.py", i * 10 + 1, i * 10 + 10))
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
        static.available_cfgs.return_value = {"python": cfg}
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
        for i, name in enumerate(("W", "X", "Y", "Z")):
            cfg.add_node(Node(name, NodeType.FUNCTION, "/src/mod.py", i * 10 + 1, i * 10 + 10))
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


class TestBuildFileMethodsFromNodes(unittest.TestCase):
    def test_deduplicates_alias_method_entries_and_keeps_more_specific_qualified_name(self):
        static = MagicMock()
        mixin = MockMixin(repo_dir=Path("/repo"), static_analysis=static)

        duplicate_specific = Node(
            "diagram_analysis.diagram_generator.DiagramGenerator.generate_analysis",
            NodeType.METHOD,
            "/repo/diagram_analysis/diagram_generator.py",
            468,
            470,
        )
        duplicate_alias = Node(
            "diagram_analysis.diagram_generator.generate_analysis",
            NodeType.METHOD,
            "/repo/diagram_analysis/diagram_generator.py",
            468,
            470,
        )

        groups = mixin._build_file_methods_from_nodes([duplicate_alias, duplicate_specific])

        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0].file_path, "diagram_analysis/diagram_generator.py")
        self.assertEqual(len(groups[0].methods), 1)
        self.assertEqual(
            groups[0].methods[0].qualified_name,
            "diagram_analysis.diagram_generator.DiagramGenerator.generate_analysis",
        )


class TestExpandToMethodLevelClusters(unittest.TestCase):
    """Test the _expand_to_method_level_clusters method."""

    def setUp(self):
        self.repo_dir = Path("/test/repo")
        self.mock_static_analysis = MagicMock()
        self.mixin = MockMixin(self.repo_dir, self.mock_static_analysis)

    def test_does_not_expand_when_enough_clusters(self):
        """Should return original cluster result when >= MIN_CLUSTERS_THRESHOLD clusters."""
        cfg = CallGraph(language="python")
        # Add some nodes
        for i in range(10):
            cfg.add_node(Node(f"mod.func_{i}", NodeType.FUNCTION, f"/test/file_{i}.py", 1, 10))

        # Create cluster result with 5 clusters (threshold)
        original_result = ClusterResult(
            clusters={i: {f"mod.func_{i}", f"mod.func_{i+5}"} for i in range(5)},
            cluster_to_files={i: {f"/test/file_{i}.py"} for i in range(5)},
            file_to_clusters={f"/test/file_{i}.py": {i % 5} for i in range(10)},
            strategy="original",
        )

        result = self.mixin._expand_to_method_level_clusters(cfg, original_result)

        # Should return the original since we have 5 clusters (= threshold)
        self.assertIs(result, original_result)

    def test_expands_when_few_clusters(self):
        """Should expand to method-level when < MIN_CLUSTERS_THRESHOLD clusters."""
        cfg = CallGraph(language="python")
        # Add 3 function nodes
        cfg.add_node(Node("mod.func_a", NodeType.FUNCTION, "/test/file_a.py", 1, 10))
        cfg.add_node(Node("mod.func_b", NodeType.FUNCTION, "/test/file_a.py", 11, 20))
        cfg.add_node(Node("mod.func_c", NodeType.FUNCTION, "/test/file_b.py", 1, 10))

        # Create cluster result with only 2 clusters (< threshold)
        original_result = ClusterResult(
            clusters={0: {"mod.func_a", "mod.func_b"}, 1: {"mod.func_c"}},
            cluster_to_files={0: {"/test/file_a.py"}, 1: {"/test/file_b.py"}},
            file_to_clusters={"/test/file_a.py": {0}, "/test/file_b.py": {1}},
            strategy="original",
        )

        result = self.mixin._expand_to_method_level_clusters(cfg, original_result)

        # Should create 3 clusters (one per function)
        self.assertEqual(len(result.clusters), 3)
        self.assertEqual(result.strategy, "method_level_expansion")
        # Each cluster should have exactly one member
        for cluster_members in result.clusters.values():
            self.assertEqual(len(cluster_members), 1)

    def test_includes_classes_when_few_callables(self):
        """Should include classes if there aren't enough callable nodes."""
        cfg = CallGraph(language="python")
        # Add only 2 function nodes and 3 class nodes
        cfg.add_node(Node("mod.func_a", NodeType.FUNCTION, "/test/file.py", 1, 10))
        cfg.add_node(Node("mod.func_b", NodeType.FUNCTION, "/test/file.py", 11, 20))
        cfg.add_node(Node("mod.ClassA", NodeType.CLASS, "/test/file.py", 21, 50))
        cfg.add_node(Node("mod.ClassB", NodeType.CLASS, "/test/file.py", 51, 100))
        cfg.add_node(Node("mod.ClassC", NodeType.CLASS, "/test/file2.py", 1, 50))

        # Create cluster result with only 1 cluster (< threshold)
        original_result = ClusterResult(
            clusters={0: {"mod.func_a", "mod.func_b", "mod.ClassA", "mod.ClassB", "mod.ClassC"}},
            cluster_to_files={0: {"/test/file.py", "/test/file2.py"}},
            file_to_clusters={"/test/file.py": {0}, "/test/file2.py": {0}},
            strategy="original",
        )

        result = self.mixin._expand_to_method_level_clusters(cfg, original_result)

        # Should create 5 clusters (2 functions + 3 classes since functions alone < threshold)
        self.assertEqual(len(result.clusters), 5)
        self.assertEqual(result.strategy, "method_level_expansion")

    def test_empty_cfg_returns_empty_clusters(self):
        """Should handle empty CFG gracefully."""
        cfg = CallGraph(language="python")

        original_result = ClusterResult(
            clusters={},
            cluster_to_files={},
            file_to_clusters={},
            strategy="empty",
        )

        result = self.mixin._expand_to_method_level_clusters(cfg, original_result)

        # Should return a new empty result with method_level_expansion strategy
        self.assertEqual(len(result.clusters), 0)
        self.assertEqual(result.strategy, "method_level_expansion")


if __name__ == "__main__":
    unittest.main()
