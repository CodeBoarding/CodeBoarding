import shutil
import unittest
from pathlib import Path
from unittest.mock import ANY, MagicMock, patch

import networkx as nx

from agents.details_agent import DetailsAgent
from agents.enrichment import StaticAnalysisEnricher
from agents.agent_responses import (
    AnalysisInsights,
    ClusterAnalysis,
    Component,
    ComponentApiSurfaces,
    ComponentRelations,
    MetaAnalysisInsights,
    SourceCodeReference,
)
from agents.file_index_models import FileMethodGroup, MethodEntry
from static_analyzer.clustering.models import ClusteringResults
from static_analyzer.clustering.service import ClusteringService

from diagram_analysis.file_index import build_files_index
from static_analyzer.analysis_result import StaticAnalysisResults
from static_analyzer.constants import NodeType
from static_analyzer.clustering.models import ClusterResult


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

    def _project_clustering(self) -> ClusteringResults:
        return ClusteringResults(
            cluster_results={},
            cfg_graphs={},
            cluster_analysis=ClusterAnalysis(cluster_groups=[]),
            static_analysis=self.mock_static_analysis,
        )

    def _make_agent(self):
        return DetailsAgent(
            repo_dir=self.repo_dir,
            clustering=self._project_clustering(),
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

    def test_init(self):
        # Test initialization
        mock_llm = MagicMock()
        mock_parsing_llm = MagicMock()
        agent = DetailsAgent(
            repo_dir=self.repo_dir,
            clustering=self._project_clustering(),
            project_name=self.project_name,
            meta_context=self.mock_meta_context,
            agent_llm=mock_llm,
            parsing_llm=mock_parsing_llm,
            run_id="test-run-id",
        )

        self.assertEqual(agent.project_name, self.project_name)
        self.assertEqual(agent.meta_context, self.mock_meta_context)
        self.assertIn("final_analysis", agent.prompts)

    @patch("agents.details_agent.DetailsAgent._invoke_repair_validate")
    def test_step_analysis_shell(self, mock_invoke_repair_validate):

        mock_llm = MagicMock()
        mock_parsing_llm = MagicMock()
        agent = DetailsAgent(
            repo_dir=self.repo_dir,
            clustering=self._project_clustering(),
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

        clustering = self._project_clustering()
        enricher = StaticAnalysisEnricher(clustering, self.repo_dir)
        result = agent.step_analysis_shell(self.test_component, clustering, enricher)

        self.assertEqual(result, mock_response)
        mock_invoke_repair_validate.assert_called_once()

    @patch("agents.details_agent.DetailsAgent._parse_invoke")
    @patch("agents.details_agent.DetailsAgent._invoke_validate")
    @patch("agents.details_agent.DetailsAgent._invoke_repair_validate")
    @patch("static_analyzer.reference_resolver.StaticReferenceResolver.fix_source_code_reference_lines")
    def test_run(self, mock_fix_ref, mock_invoke_repair_validate, mock_invoke_validate, mock_parse_invoke):
        mock_llm = MagicMock()
        mock_parsing_llm = MagicMock()
        agent = DetailsAgent(
            repo_dir=self.repo_dir,
            clustering=self._project_clustering(),
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
        mock_subgraph.cluster_cache = sub_cluster_result
        mock_subgraph.to_cluster_string.return_value = "Component CFG String"
        mock_subgraph.to_networkx.return_value = subgraph_graph
        mock_subgraph.clustering_networkx.return_value = subgraph_graph

        mock_cfg = MagicMock()
        mock_cfg.cluster_cache = mock_cluster_result
        mock_cfg.filter_by_nodes.return_value = mock_subgraph
        # _build_cluster_string calls cfg.to_cluster_string on the original cfg
        mock_cfg.to_cluster_string.return_value = "Cluster 1: method_a, method_b"
        # The deterministic grouping reads the (super-)graph via clustering_networkx()
        mock_cfg.to_networkx.return_value = subgraph_graph
        mock_cfg.clustering_networkx.return_value = subgraph_graph

        self.mock_static_analysis.get_languages.return_value = ["python"]
        self.mock_static_analysis.get_cfg.return_value = mock_cfg

        # Mock responses for final analysis. Grouping is deterministic (done by the
        # clustering service), so the only _invoke_validate call is for relations.
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

        # Clustering happens in its own stage; the agent consumes the results.
        clustering = ClusteringService(self.repo_dir, self.mock_static_analysis).cluster_component(self.test_component)
        analysis = agent.run(self.test_component, clustering)

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
