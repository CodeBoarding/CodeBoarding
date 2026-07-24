import shutil
import unittest
from pathlib import Path
from unittest.mock import ANY, MagicMock, patch

import networkx as nx

from agents.details_agent import DetailsAgent
from agents.agent_responses import (
    AnalysisInsights,
    ClusterAnalysis,
    ClustersComponent,
    Component,
    ComponentApiSurfaces,
    ComponentRelations,
    MetaAnalysisInsights,
    SourceCodeReference,
)
from agents.file_index_models import FileMethodGroup, MethodEntry

from diagram_analysis.file_index import build_files_index
from static_analyzer.analysis_result import StaticAnalysisResults
from static_analyzer.constants import NodeType
from static_analyzer.graph import CallGraph, ClusterResult
from static_analyzer.node import Node


class TestDetailsAgent(unittest.TestCase):
    def setUp(self):
        # Create mock static analysis
        self.mock_static_analysis = MagicMock(spec=StaticAnalysisResults)
        self.mock_static_analysis.get_languages.return_value = ["python"]

        # Create mock meta context
        self.mock_meta_context = MetaAnalysisInsights(
            project_type="library",
            domain="software development",
            architectural_patterns=["layered architecture"],
            expected_components=["core", "utils"],
            technology_stack=["Python"],
            architectural_bias="Focus on modularity",
        )

        import tempfile

        self.temp_dir = tempfile.mkdtemp()
        self.repo_dir = Path(self.temp_dir) / "test_repo"
        self.repo_dir.mkdir(parents=True, exist_ok=True)
        self.project_name = "test_project"

        # Create test component
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

    def tearDown(self):
        if hasattr(self, "temp_dir"):
            shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _make_agent(self):
        return DetailsAgent(
            repo_dir=self.repo_dir,
            static_analysis=self.mock_static_analysis,
            project_name=self.project_name,
            meta_context=self.mock_meta_context,
            agent_llm=MagicMock(),
            parsing_llm=MagicMock(),
            run_id="test-run-id",
        )

    @staticmethod
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

    def test_init(self):
        # Test initialization
        mock_llm = MagicMock()
        mock_parsing_llm = MagicMock()
        agent = DetailsAgent(
            repo_dir=self.repo_dir,
            static_analysis=self.mock_static_analysis,
            project_name=self.project_name,
            meta_context=self.mock_meta_context,
            agent_llm=mock_llm,
            parsing_llm=mock_parsing_llm,
            run_id="test-run-id",
        )

        self.assertEqual(agent.project_name, self.project_name)
        self.assertEqual(agent.meta_context, self.mock_meta_context)
        self.assertIn("final_analysis", agent.prompts)

    def test_create_strict_component_subgraph(self):
        # Test creating subgraph from component assigned files
        mock_llm = MagicMock()
        mock_parsing_llm = MagicMock()
        agent = DetailsAgent(
            repo_dir=self.repo_dir,
            static_analysis=self.mock_static_analysis,
            project_name=self.project_name,
            meta_context=self.mock_meta_context,
            agent_llm=mock_llm,
            parsing_llm=mock_parsing_llm,
            run_id="test-run-id",
        )
        # Build the expected set of qualified names from component's file_methods
        expected_qnames = {"test.func", "test_utils.helper"}

        # Create a mock node with proper attributes for method-level expansion check
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

        self.mock_static_analysis.get_languages.return_value = ["python"]
        self.mock_static_analysis.get_cfg.return_value = mock_cfg

        subgraph_cluster_results, subgraph_cfgs = agent._create_strict_component_subgraph(self.test_component)

        self.assertIs(subgraph_cluster_results["python"], mock_sub_cluster_result)
        self.assertIn("python", subgraph_cfgs)
        self.assertIs(subgraph_cfgs["python"], mock_subgraph)
        self.mock_static_analysis.get_cfg.assert_called_with("python")
        mock_cfg.filter_by_nodes.assert_called_with(expected_qnames)
        mock_subgraph.cluster.assert_called_once()

    def test_step_clusters_grouping(self):
        # Grouping is deterministic (resolution-tuned Leiden on the subgraph), no LLM call.
        agent = self._make_agent()
        cr, graph = self._clustered_graph(range(1, 11))
        subgraph_cfg = MagicMock()
        subgraph_cfg.clustering_networkx.return_value = graph
        subgraph_cluster_results = {"python": cr}
        subgraph_cfgs = {"python": subgraph_cfg}

        result = agent.step_clusters_grouping(self.test_component, subgraph_cluster_results, subgraph_cfgs)
        result_again = agent.step_clusters_grouping(self.test_component, subgraph_cluster_results, subgraph_cfgs)

        # A complete, disjoint partition of the leaf clusters into "Group i" components.
        self._assert_partition(result, list(range(1, 11)))
        # Deterministic: same membership on a re-run.
        self.assertEqual(
            [sorted(cc.cluster_ids) for cc in result.cluster_components],
            [sorted(cc.cluster_ids) for cc in result_again.cluster_components],
        )

    @patch("agents.details_agent.DetailsAgent._invoke_repair_validate")
    def test_step_final_analysis(self, mock_invoke_repair_validate):
        # Test step_final_analysis
        mock_llm = MagicMock()
        mock_parsing_llm = MagicMock()
        agent = DetailsAgent(
            repo_dir=self.repo_dir,
            static_analysis=self.mock_static_analysis,
            project_name=self.project_name,
            meta_context=self.mock_meta_context,
            agent_llm=mock_llm,
            parsing_llm=mock_parsing_llm,
            run_id="test-run-id",
        )
        mock_response = AnalysisInsights(
            description="Structure analysis",
            components=[],
            components_relations=[],
        )
        mock_invoke_repair_validate.return_value = mock_response

        cluster_analysis = ClusterAnalysis(cluster_components=[])
        result = agent.step_final_analysis(self.test_component, cluster_analysis, {}, {})

        self.assertEqual(result, mock_response)
        mock_invoke_repair_validate.assert_called_once()

    def test_resolve_cluster_ids_from_groups(self):
        # Test _resolve_cluster_ids_from_groups
        mock_llm = MagicMock()
        mock_parsing_llm = MagicMock()
        agent = DetailsAgent(
            repo_dir=self.repo_dir,
            static_analysis=self.mock_static_analysis,
            project_name=self.project_name,
            meta_context=self.mock_meta_context,
            agent_llm=mock_llm,
            parsing_llm=mock_parsing_llm,
            run_id="test-run-id",
        )

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

        agent._resolve_cluster_ids_from_groups(analysis, cluster_analysis)

        self.assertEqual(analysis.components[0].source_cluster_ids, ["1", "2", "3", "4"])
        self.assertEqual(analysis.components[1].source_cluster_ids, ["1", "2"])

    def test_resolve_cluster_ids_from_groups_case_insensitive(self):
        # Test case-insensitive fallback
        mock_llm = MagicMock()
        mock_parsing_llm = MagicMock()
        agent = DetailsAgent(
            repo_dir=self.repo_dir,
            static_analysis=self.mock_static_analysis,
            project_name=self.project_name,
            meta_context=self.mock_meta_context,
            agent_llm=mock_llm,
            parsing_llm=mock_parsing_llm,
            run_id="test-run-id",
        )

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

        agent._resolve_cluster_ids_from_groups(analysis, cluster_analysis)

        self.assertEqual(analysis.components[0].source_cluster_ids, ["1", "2"])

    def test_static_relation_pass_qualifies_detail_cluster_ids_with_parent_component_id(self):
        mock_llm = MagicMock()
        mock_parsing_llm = MagicMock()
        agent = DetailsAgent(
            repo_dir=self.repo_dir,
            static_analysis=self.mock_static_analysis,
            project_name=self.project_name,
            meta_context=self.mock_meta_context,
            agent_llm=mock_llm,
            parsing_llm=mock_parsing_llm,
            run_id="test-run-id",
        )
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

        agent.build_static_relations(analysis, {}, source_cluster_id_prefix="5.3")

        self.assertEqual(analysis.components[0].source_cluster_ids, ["5.3.1", "5.3.2"])
        self.assertEqual(analysis.components[1].source_cluster_ids, ["5.3.7"])

    @patch("agents.details_agent.DetailsAgent._parse_invoke")
    @patch("agents.details_agent.DetailsAgent._invoke_validate")
    @patch("agents.details_agent.DetailsAgent._invoke_repair_validate")
    @patch("static_analyzer.reference_resolver.StaticReferenceResolver.fix_source_code_reference_lines")
    def test_run(self, mock_fix_ref, mock_invoke_repair_validate, mock_invoke_validate, mock_parse_invoke):
        mock_llm = MagicMock()
        mock_parsing_llm = MagicMock()
        agent = DetailsAgent(
            repo_dir=self.repo_dir,
            static_analysis=self.mock_static_analysis,
            project_name=self.project_name,
            meta_context=self.mock_meta_context,
            agent_llm=mock_llm,
            parsing_llm=mock_parsing_llm,
            run_id="test-run-id",
        )
        # Mock StaticAnalysis and CFG behavior for run
        abs_assigned = {str(self.repo_dir / fg.file_path) for fg in self.test_component.file_methods}
        mock_cluster_result = MagicMock()
        mock_cluster_result.get_cluster_ids.return_value = {1}
        mock_cluster_result.get_files_for_cluster.return_value = abs_assigned

        # Real subgraph cluster result + graph so deterministic grouping has structure.
        sub_cluster_result, subgraph_graph = self._clustered_graph(range(1, 7))

        mock_node = MagicMock()
        mock_node.file_path = str(self.repo_dir / "src" / "main.py")
        mock_node.fully_qualified_name = "n1"
        mock_node.type = NodeType.FUNCTION
        mock_node.line_start = 1
        mock_node.line_end = 10

        mock_subgraph = MagicMock()
        mock_subgraph.nodes = {"n1": mock_node}
        mock_subgraph.cluster.return_value = sub_cluster_result
        mock_subgraph.to_cluster_string.return_value = "Component CFG String"
        mock_subgraph.to_networkx.return_value = subgraph_graph

        mock_cfg = MagicMock()
        mock_cfg.cluster.return_value = mock_cluster_result
        mock_cfg.filter_by_nodes.return_value = mock_subgraph
        # _build_cluster_string calls cfg.to_cluster_string on the original cfg
        mock_cfg.to_cluster_string.return_value = "Cluster 1: method_a, method_b"
        # deterministic_cluster_grouping reads the (super-)graph via get_cfg(...).to_networkx()
        mock_cfg.to_networkx.return_value = subgraph_graph

        self.mock_static_analysis.get_languages.return_value = ["python"]
        self.mock_static_analysis.get_cfg.return_value = mock_cfg

        # Mock responses for final analysis. Grouping is now deterministic, so the
        # only _invoke_validate call in the pipeline is for relations.
        final_component = Component(
            name="SubComp",
            description="A sub-component",
            key_entities=[],
            source_group_names=[],
        )
        final_response = AnalysisInsights(
            description="Final",
            components=[final_component],
            components_relations=[],
        )

        api_response = ComponentApiSurfaces(api_surfaces=[])
        relation_response = ComponentRelations(components_relations=[])
        mock_invoke_validate.side_effect = [relation_response]
        mock_invoke_repair_validate.return_value = final_response
        mock_parse_invoke.return_value = api_response
        mock_fix_ref.return_value = final_response

        analysis, _subgraph_results = agent.run(self.test_component)

        self.assertEqual(analysis, final_response)
        mock_invoke_validate.assert_called_once_with(
            ANY,
            ComponentRelations,
            validators=ANY,
            validation_context=ANY,
            max_validation_attempts=3,
        )
        mock_invoke_repair_validate.assert_called_once()
        mock_parse_invoke.assert_called_once_with(ANY, ComponentApiSurfaces)
        mock_fix_ref.assert_called_once()

    def test_populate_file_methods(self):
        # Test deterministic file population from cluster results
        mock_llm = MagicMock()
        mock_parsing_llm = MagicMock()
        agent = DetailsAgent(
            repo_dir=self.repo_dir,
            static_analysis=self.mock_static_analysis,
            project_name=self.project_name,
            meta_context=self.mock_meta_context,
            agent_llm=mock_llm,
            parsing_llm=mock_parsing_llm,
            run_id="test-run-id",
        )

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
        self.mock_static_analysis.get_cfg.return_value = call_graph

        cluster_result = ClusterResult(
            clusters={1: {"pkg.cluster_fn", "pkg.TestClass"}},
            file_to_clusters={str(cluster_file): {1}, str(test_file): {1}},
            cluster_to_files={1: {str(cluster_file), str(test_file)}},
            strategy="test",
        )
        cluster_results = {"python": cluster_result}

        agent.populate_file_methods(analysis, cluster_results)

        self.assertEqual([group.file_path for group in sub_component.file_methods], ["cluster_file.py", "test_file.py"])
        self.assertEqual(sub_component.file_methods[0].methods[0].qualified_name, "pkg.cluster_fn")
        self.assertEqual(sub_component.file_methods[1].methods[0].qualified_name, "pkg.TestClass")

    def test_build_files_index_merges_shared_file_methods(self):
        component_a = Component(
            name="CompA",
            description="A",
            key_entities=[],
            file_methods=[
                FileMethodGroup(
                    file_path="shared.py",
                    methods=[
                        MethodEntry(
                            qualified_name="pkg.shared.alpha",
                            start_line=1,
                            end_line=5,
                            node_type="FUNCTION",
                        )
                    ],
                )
            ],
        )
        component_b = Component(
            name="CompB",
            description="B",
            key_entities=[],
            file_methods=[
                FileMethodGroup(
                    file_path="shared.py",
                    methods=[
                        MethodEntry(
                            qualified_name="pkg.shared.beta",
                            start_line=10,
                            end_line=15,
                            node_type="FUNCTION",
                        )
                    ],
                )
            ],
        )

        analysis = AnalysisInsights(
            description="Test analysis",
            components=[component_a, component_b],
            components_relations=[],
        )

        files_index = build_files_index(analysis, self.repo_dir)

        self.assertIn("shared.py", files_index)
        self.assertEqual(
            [method.qualified_name for method in files_index["shared.py"].methods],
            ["pkg.shared.alpha", "pkg.shared.beta"],
        )


if __name__ == "__main__":
    unittest.main()
