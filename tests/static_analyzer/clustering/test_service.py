import unittest
from pathlib import Path
from unittest.mock import MagicMock

import networkx as nx

from agents.agent_responses import ClusterAnalysis, Component, SourceCodeReference
from agents.file_index_models import FileMethodGroup, MethodEntry
from static_analyzer.clustering.cluster_helpers import SUBCOMPONENTS_MAX, SUBCOMPONENTS_MIN
from static_analyzer.clustering.service import ClusteringService, _expand_to_method_level_clusters
from static_analyzer import StaticAnalysisFatalError
from static_analyzer.analysis_result import StaticAnalysisResults
from static_analyzer.constants import NodeType
from static_analyzer.clustering.models import ClusterResult
from static_analyzer.graph import CallGraph
from static_analyzer.node import Node


def _clustered_graph(cluster_ids):
    """A ClusterResult + matching nx graph: one chained pair of nodes per cluster id."""
    clusters, cluster_to_files, file_to_clusters = {}, {}, {}
    graph = nx.DiGraph()
    for cid in cluster_ids:
        nodes = [f"pkg.mod{cid}.a", f"pkg.mod{cid}.b"]
        clusters[cid] = set(nodes)
        path = f"/repo/mod{cid}.py"
        cluster_to_files[cid] = {path}
        file_to_clusters[path] = {cid}
        for node in nodes:
            graph.add_node(node, file_path=path)
        graph.add_edge(nodes[0], nodes[1])
    # Chain consecutive clusters so the meta-graph is connected.
    ids = list(cluster_ids)
    for prev, cur in zip(ids, ids[1:]):
        graph.add_edge(f"pkg.mod{prev}.b", f"pkg.mod{cur}.a")
    cr = ClusterResult(
        clusters=clusters, cluster_to_files=cluster_to_files, file_to_clusters=file_to_clusters, strategy="test"
    )
    return cr, graph


class TestGroupClusters(unittest.TestCase):
    def setUp(self):
        self.mock_static_analysis = MagicMock(spec=StaticAnalysisResults)
        self.service = ClusteringService(Path("/repo"), self.mock_static_analysis)

    def _assert_partition(self, result, expected_ids):
        self.assertIsInstance(result, ClusterAnalysis)
        self.assertGreaterEqual(len(result.cluster_components), 1)
        # Names are the deterministic Group-1..N labels.
        self.assertEqual(
            [cc.name for cc in result.cluster_components],
            [f"Group {i}" for i in range(1, len(result.cluster_components) + 1)],
        )
        # Every leaf cluster is owned by exactly one group (a true, disjoint partition).
        assigned = [cid for cc in result.cluster_components for cid in cc.cluster_ids]
        self.assertEqual(sorted(assigned), sorted(expected_ids))
        self.assertEqual(len(assigned), len(set(assigned)))

    def test_group_clusters_single_language(self):
        cr, graph = _clustered_graph(range(1, 13))
        cluster_results = {"python": cr}

        result = self.service._group_clusters(cluster_results, {"python": graph})
        result_again = self.service._group_clusters(cluster_results, {"python": graph})

        self._assert_partition(result, list(range(1, 13)))
        # Deterministic: same membership on a re-run.
        self.assertEqual(
            [sorted(cc.cluster_ids) for cc in result.cluster_components],
            [sorted(cc.cluster_ids) for cc in result_again.cluster_components],
        )

    def test_group_clusters_multiple_languages(self):
        # Globally-unique cluster ids across languages, sharing one combined graph.
        _, graph = _clustered_graph(range(1, 13))
        py_cr, _ = _clustered_graph(range(1, 7))
        js_cr, _ = _clustered_graph(range(7, 13))
        cluster_results = {"python": py_cr, "javascript": js_cr}

        result = self.service._group_clusters(cluster_results, {"python": graph, "javascript": graph})

        self._assert_partition(result, list(range(1, 13)))

    def test_group_clusters_no_languages(self):
        result = self.service._group_clusters({}, {})

        self.assertIsInstance(result, ClusterAnalysis)
        self.assertEqual(result.cluster_components, [])

    def test_group_clusters_subcomponent_range(self):
        cr, graph = _clustered_graph(range(1, 11))

        result = self.service._group_clusters({"python": cr}, {"python": graph}, SUBCOMPONENTS_MIN, SUBCOMPONENTS_MAX)
        result_again = self.service._group_clusters(
            {"python": cr}, {"python": graph}, SUBCOMPONENTS_MIN, SUBCOMPONENTS_MAX
        )

        self._assert_partition(result, list(range(1, 11)))
        self.assertEqual(
            [sorted(cc.cluster_ids) for cc in result.cluster_components],
            [sorted(cc.cluster_ids) for cc in result_again.cluster_components],
        )

    def test_cluster_project_raises_on_empty_structure(self):
        self.mock_static_analysis.get_languages.return_value = []

        with self.assertRaises(StaticAnalysisFatalError):
            self.service.cluster_project()


class TestComponentSubgraph(unittest.TestCase):
    def setUp(self):
        self.mock_static_analysis = MagicMock(spec=StaticAnalysisResults)
        self.mock_static_analysis.get_languages.return_value = ["python"]
        self.repo_dir = Path("/repo")
        self.service = ClusteringService(self.repo_dir, self.mock_static_analysis)

        ref = SourceCodeReference(
            qualified_name="test.TestClass",
            reference_file="test.py",
            reference_start_line=1,
            reference_end_line=10,
        )
        self.test_component = Component(
            name="TestComponent",
            description="Test component",
            key_entities=[ref],
            file_methods=[
                FileMethodGroup(
                    file_path="test.py",
                    methods=[
                        MethodEntry(qualified_name="test.func", start_line=1, end_line=10, node_type="FUNCTION"),
                    ],
                ),
                FileMethodGroup(
                    file_path="test_utils.py",
                    methods=[
                        MethodEntry(qualified_name="test_utils.helper", start_line=1, end_line=5, node_type="FUNCTION"),
                    ],
                ),
            ],
        )

    def test_component_subgraph_filters_by_component_methods(self):
        expected_qnames = {"test.func", "test_utils.helper"}

        mock_node = MagicMock()
        mock_node.type = NodeType.FUNCTION
        mock_node.file_path = str(self.repo_dir / "test.py")
        mock_node.fully_qualified_name = "test.func"

        # Mock cluster result with enough clusters to skip method-level expansion
        mock_sub_cluster_result = ClusterResult(
            clusters={i: {f"method_{i}"} for i in range(5)},  # 5 clusters to skip expansion
            cluster_to_files={i: {str(self.repo_dir / "test.py")} for i in range(5)},
            file_to_clusters={str(self.repo_dir / "test.py"): set(range(5))},
            strategy="test",
        )

        mock_subgraph = MagicMock()
        mock_subgraph.nodes = {"n1": mock_node}
        mock_subgraph.cluster.return_value = mock_sub_cluster_result
        mock_subgraph.method_cluster_paths_snapshot.return_value = []

        mock_cfg = MagicMock()
        mock_cfg.filter_by_nodes.return_value = mock_subgraph

        self.mock_static_analysis.get_cfg.return_value = mock_cfg

        subgraph_cluster_results, subgraph_cfgs = self.service._component_subgraph(self.test_component)

        self.assertIs(subgraph_cluster_results["python"], mock_sub_cluster_result)
        self.assertIn("python", subgraph_cfgs)
        self.assertIs(subgraph_cfgs["python"], mock_subgraph)
        self.mock_static_analysis.get_cfg.assert_called_with("python")
        mock_cfg.filter_by_nodes.assert_called_with(expected_qnames)
        mock_subgraph.cluster.assert_called_once()

    def test_component_without_methods_yields_empty_clustering(self):
        empty = Component(name="Empty", description="", key_entities=[])

        cluster_results, subgraph_cfgs = self.service._component_subgraph(empty)

        self.assertEqual(cluster_results, {})
        self.assertEqual(subgraph_cfgs, {})


class TestExpandToMethodLevelClusters(unittest.TestCase):
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

        result = _expand_to_method_level_clusters(cfg, original_result)

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

        result = _expand_to_method_level_clusters(cfg, original_result)

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

        result = _expand_to_method_level_clusters(cfg, original_result)

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

        result = _expand_to_method_level_clusters(cfg, original_result)

        # Should return a new empty result with method_level_expansion strategy
        self.assertEqual(len(result.clusters), 0)
        self.assertEqual(result.strategy, "method_level_expansion")


if __name__ == "__main__":
    unittest.main()
