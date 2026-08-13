import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

from agents.agent_responses import (
    AnalysisInsights,
    ClusterAnalysis,
    ClustersComponent,
    Component,
)
from agents.file_index_models import FileMethodGroup, MethodEntry
from agents.enrichment import (
    StaticAnalysisEnricher,
    _build_file_methods_from_nodes,
    _build_undirected_graphs,
    _find_nearest_cluster,
    build_scope_cfg_string,
    build_static_relations,
)
from static_analyzer.clustering.service import ClusteringResults
from static_analyzer.clustering.models import ClusterResult
from static_analyzer.graph import CallGraph
from static_analyzer.constants import NodeType
from static_analyzer.node import Node


def _enricher(
    cluster_results: dict | None = None,
    cluster_analysis: ClusterAnalysis | None = None,
    static=None,
    repo_dir: Path = Path("/repo"),
) -> StaticAnalysisEnricher:
    """Enricher over a minimal ClusteringResults scope for these tests."""
    clustering = ClusteringResults(
        cluster_results=cluster_results or {},
        cfg_graphs={},
        cluster_analysis=cluster_analysis or ClusterAnalysis(cluster_components=[]),
        static_analysis=static if static is not None else MagicMock(),
    )
    return StaticAnalysisEnricher(clustering, repo_dir)


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

        rendered = build_scope_cfg_string(analysis, static)

        self.assertIn("A -> B (12 edges):", rendered)
        self.assertIn("... and 2 more", rendered)
        self.assertEqual(rendered.count("  f"), 10)


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

    def _make_static(self, cfg: CallGraph) -> MagicMock:
        static = MagicMock()
        static.get_cfg.return_value = cfg
        static.available_cfgs.return_value = {"python": cfg}
        return static

    def test_finds_nearest_cluster_by_graph_distance(self):
        """C is 1 hop from both clusters; cluster 2 members D,E are direct neighbours."""
        cfg = self._make_call_graph()
        cr = self._make_cluster_result()
        cluster_results = {"python": cr}
        static = self._make_static(cfg)

        undirected_graphs = _build_undirected_graphs(cluster_results, static)
        # C is distance-1 from D (cluster 2) and distance-1 from B (cluster 1).
        # Both clusters have a member at distance 1, so the first one found wins
        # (deterministic dict order).
        result = _find_nearest_cluster("C", cluster_results, undirected_graphs)
        self.assertIn(result, {1, 2})

    def test_returns_none_for_disconnected_node(self):
        """A node not in any graph returns None."""
        cfg = self._make_call_graph()
        # Add an isolated node
        cfg.add_node(Node("Z", NodeType.FUNCTION, "/src/other.py", 1, 5))
        cr = self._make_cluster_result()
        cluster_results = {"python": cr}
        static = self._make_static(cfg)

        undirected_graphs = _build_undirected_graphs(cluster_results, static)
        result = _find_nearest_cluster("Z", cluster_results, undirected_graphs)
        self.assertIsNone(result)

    def test_returns_none_when_node_not_in_graph(self):
        """A node name absent from the graph entirely returns None."""
        cfg = self._make_call_graph()
        cr = self._make_cluster_result()
        cluster_results = {"python": cr}
        static = self._make_static(cfg)

        undirected_graphs = _build_undirected_graphs(cluster_results, static)
        result = _find_nearest_cluster("NONEXISTENT", cluster_results, undirected_graphs)
        self.assertIsNone(result)

    def test_node_inside_cluster_returns_own_cluster(self):
        """A node that is itself a cluster member should return its own cluster (distance 0)."""
        cfg = self._make_call_graph()
        cr = self._make_cluster_result()
        cluster_results = {"python": cr}
        static = self._make_static(cfg)

        undirected_graphs = _build_undirected_graphs(cluster_results, static)
        result = _find_nearest_cluster("A", cluster_results, undirected_graphs)
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
        static = self._make_static(cfg)

        undirected_graphs = _build_undirected_graphs(cluster_results, static)
        result = _find_nearest_cluster("W", cluster_results, undirected_graphs)
        self.assertEqual(result, 10)


class TestBuildFileMethodsFromNodes(unittest.TestCase):
    def test_deduplicates_alias_method_entries_and_keeps_more_specific_qualified_name(self):
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

        groups = _build_file_methods_from_nodes([duplicate_alias, duplicate_specific], Path("/repo"))

        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0].file_path, "diagram_analysis/diagram_generator.py")
        self.assertEqual(len(groups[0].methods), 1)
        self.assertEqual(
            groups[0].methods[0].qualified_name,
            "diagram_analysis.diagram_generator.DiagramGenerator.generate_analysis",
        )


class TestResolveClusterIdsFromGroups(unittest.TestCase):
    def test_resolve_cluster_ids_from_groups(self):
        cluster_analysis = ClusterAnalysis(
            cluster_components=[
                ClustersComponent(name="GroupA", cluster_ids=[1, 2], description="Group A"),
                ClustersComponent(name="GroupB", cluster_ids=[3, 4], description="Group B"),
            ]
        )

        analysis = AnalysisInsights(
            description="Test",
            components=[
                Component(
                    name="Comp1",
                    description="Comp1",
                    key_entities=[],
                    source_group_names=["GroupA", "GroupB"],
                ),
                Component(
                    name="Comp2",
                    description="Comp2",
                    key_entities=[],
                    source_group_names=["GroupA"],
                ),
            ],
            components_relations=[],
        )

        _enricher(cluster_analysis=cluster_analysis).resolve_cluster_ids(analysis)

        self.assertEqual(analysis.components[0].source_cluster_ids, ["1", "2", "3", "4"])
        self.assertEqual(analysis.components[1].source_cluster_ids, ["1", "2"])

    def test_resolve_cluster_ids_from_groups_case_insensitive(self):
        cluster_analysis = ClusterAnalysis(
            cluster_components=[
                ClustersComponent(name="GroupA", cluster_ids=[1, 2], description="Group A"),
            ]
        )

        analysis = AnalysisInsights(
            description="Test",
            components=[
                Component(
                    name="Comp1",
                    description="Comp1",
                    key_entities=[],
                    source_group_names=["groupa"],
                ),
            ],
            components_relations=[],
        )

        _enricher(cluster_analysis=cluster_analysis).resolve_cluster_ids(analysis)

        self.assertEqual(analysis.components[0].source_cluster_ids, ["1", "2"])


class TestBuildStaticRelations(unittest.TestCase):
    def test_static_relation_pass_qualifies_detail_cluster_ids_with_parent_component_id(self):
        analysis = AnalysisInsights(
            description="Test",
            components=[
                Component(
                    name="ChildA",
                    description="ChildA",
                    key_entities=[],
                    source_cluster_ids=["1", "2"],
                ),
                Component(
                    name="ChildB",
                    description="ChildB",
                    key_entities=[],
                    source_cluster_ids=["7"],
                ),
            ],
            components_relations=[],
        )

        build_static_relations(analysis, MagicMock(), {}, source_cluster_id_prefix="5.3")

        self.assertEqual(analysis.components[0].source_cluster_ids, ["5.3.1", "5.3.2"])
        self.assertEqual(analysis.components[1].source_cluster_ids, ["5.3.7"])


class TestPopulateFileMethods(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.repo_dir = Path(self.temp_dir) / "test_repo"
        self.repo_dir.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_populate_file_methods(self):
        static = MagicMock()
        static.get_languages.return_value = ["python"]

        sub_component = Component(
            name="SubComponent",
            description="Sub component",
            key_entities=[],
            source_cluster_ids=["1"],
        )
        sub_component.component_id = "1"

        analysis = AnalysisInsights(
            description="Test analysis",
            components=[sub_component],
            components_relations=[],
        )

        cluster_file = self.repo_dir / "cluster_file.py"
        test_file = self.repo_dir / "test_file.py"
        call_graph = CallGraph(language="python")
        call_graph.add_node(Node("pkg.cluster_fn", NodeType.FUNCTION, str(cluster_file), 1, 5))
        call_graph.add_node(Node("pkg.TestClass", NodeType.CLASS, str(test_file), 1, 10))
        static.get_cfg.return_value = call_graph

        cluster_result = ClusterResult(
            clusters={1: {"pkg.cluster_fn", "pkg.TestClass"}},
            file_to_clusters={str(cluster_file): {1}, str(test_file): {1}},
            cluster_to_files={1: {str(cluster_file), str(test_file)}},
            strategy="test",
        )
        cluster_results = {"python": cluster_result}

        _enricher(cluster_results=cluster_results, static=static, repo_dir=self.repo_dir).populate_file_methods(
            analysis
        )

        self.assertEqual([group.file_path for group in sub_component.file_methods], ["cluster_file.py", "test_file.py"])
        self.assertEqual(sub_component.file_methods[0].methods[0].qualified_name, "pkg.cluster_fn")
        self.assertEqual(sub_component.file_methods[1].methods[0].qualified_name, "pkg.TestClass")


if __name__ == "__main__":
    unittest.main()
