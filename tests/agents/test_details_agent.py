from typing import cast
import unittest
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch, PropertyMock

from agents.details_agent import DetailsAgent
from agents.agent_responses import (
    AnalysisInsights,
    ClusterAnalysis,
    ClustersComponent,
    Component,
    FileMethodGroup,
    MetaAnalysisInsights,
    SourceCodeReference,
)

from static_analyzer.graph import ClusterResult
from static_analyzer.node import Node
from static_analyzer.constants import NodeType
from static_analyzer.analysis_result import StaticAnalysisResults


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
                FileMethodGroup(file_path="test.py"),
                FileMethodGroup(file_path="test_utils.py"),
            ],
        )

    def tearDown(self):
        import shutil

        if hasattr(self, "temp_dir"):
            shutil.rmtree(self.temp_dir, ignore_errors=True)

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
        self.assertIn("group_clusters", agent.prompts)
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
        # Mock StaticAnalysis and CFG behavior
        abs_assigned = {str(self.repo_dir / fg.file_path) for fg in self.test_component.file_methods}
        mock_cluster_result = MagicMock()
        mock_cluster_result.get_cluster_ids.return_value = {1}
        mock_cluster_result.get_files_for_cluster.return_value = abs_assigned

        mock_sub_cluster_result = MagicMock()

        mock_subgraph = MagicMock()
        mock_subgraph.nodes = {"n1": object()}
        mock_subgraph.cluster.return_value = mock_sub_cluster_result
        mock_subgraph.to_cluster_string.return_value = "Component CFG String"

        mock_cfg = MagicMock()
        mock_cfg.cluster.return_value = mock_cluster_result
        # Ensure filter_by_files returns our mock subgraph
        mock_cfg.filter_by_files.return_value = mock_subgraph

        self.mock_static_analysis.get_languages.return_value = ["python"]
        self.mock_static_analysis.get_cfg.return_value = mock_cfg

        subgraph_str, subgraph_cluster_results = agent._create_strict_component_subgraph(self.test_component)

        self.assertIn("Component CFG String", subgraph_str)
        self.mock_static_analysis.get_cfg.assert_called_with("python")
        mock_cfg.filter_by_files.assert_called_with(abs_assigned)
        mock_subgraph.cluster.assert_called_once()

    @patch("agents.details_agent.DetailsAgent._validation_invoke")
    def test_step_clusters_grouping(self, mock_validation_invoke):
        # Test step_clusters_grouping
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
        mock_response = ClusterAnalysis(cluster_components=[])
        mock_validation_invoke.return_value = mock_response

        # Mock CFG to return a proper cluster string
        mock_cfg = MagicMock()
        mock_cfg.to_cluster_string.return_value = "Cluster 1: method_a, method_b"
        self.mock_static_analysis.get_cfg.return_value = mock_cfg

        mock_cluster_result = MagicMock()
        subgraph_cluster_results = {"python": mock_cluster_result}

        result = agent.step_clusters_grouping(self.test_component, subgraph_cluster_results)

        self.assertEqual(result, mock_response)
        mock_validation_invoke.assert_called_once()

    @patch("agents.details_agent.DetailsAgent._validation_invoke")
    def test_step_final_analysis(self, mock_validation_invoke):
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
        mock_validation_invoke.return_value = mock_response

        cluster_analysis = ClusterAnalysis(cluster_components=[])
        result = agent.step_final_analysis(self.test_component, cluster_analysis, {})

        self.assertEqual(result, mock_response)
        mock_validation_invoke.assert_called_once()

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

        self.assertEqual(analysis.components[0].source_cluster_ids, [1, 2, 3, 4])
        self.assertEqual(analysis.components[1].source_cluster_ids, [1, 2])

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

        self.assertEqual(analysis.components[0].source_cluster_ids, [1, 2])

    @patch("agents.details_agent.DetailsAgent._validation_invoke")
    @patch("agents.details_agent.DetailsAgent.fix_source_code_reference_lines")
    def test_run(self, mock_fix_ref, mock_validation_invoke):
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

        mock_sub_cluster_result = MagicMock()

        mock_subgraph = MagicMock()
        mock_subgraph.nodes = {"n1": object()}
        mock_subgraph.cluster.return_value = mock_sub_cluster_result
        mock_subgraph.to_cluster_string.return_value = "Component CFG String"

        mock_cfg = MagicMock()
        mock_cfg.cluster.return_value = mock_cluster_result
        mock_cfg.filter_by_files.return_value = mock_subgraph
        # _build_cluster_string calls cfg.to_cluster_string on the original cfg
        mock_cfg.to_cluster_string.return_value = "Cluster 1: method_a, method_b"

        self.mock_static_analysis.get_languages.return_value = ["python"]
        self.mock_static_analysis.get_cfg.return_value = mock_cfg

        # Mock responses for grouping and final analysis
        cluster_response = ClusterAnalysis(cluster_components=[])
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

        mock_validation_invoke.side_effect = [cluster_response, final_response]
        mock_fix_ref.return_value = final_response

        analysis, subgraph_results = agent.run(self.test_component)

        self.assertEqual(analysis, final_response)
        self.assertEqual(mock_validation_invoke.call_count, 2)
        mock_fix_ref.assert_called_once()

    def test_classify_files(self):
        # Test populate_file_methods assigns files from cluster results via CFG nodes
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

        cluster_file_path = str(self.repo_dir / "cluster_file.py")
        node = Node(
            fully_qualified_name="node1",
            node_type=NodeType.METHOD,
            file_path=cluster_file_path,
            line_start=1,
            line_end=10,
        )

        mock_cfg = MagicMock()
        mock_cfg.nodes = {"node1": node}
        self.mock_static_analysis.get_cfg.return_value = mock_cfg

        sub_component = Component(
            name="SubComponent",
            description="Sub component",
            key_entities=[],
            source_cluster_ids=[1],
        )

        analysis = AnalysisInsights(
            description="Test analysis",
            components=[sub_component],
            components_relations=[],
        )

        mock_cluster_result = ClusterResult(
            clusters={1: {"node1"}},
            file_to_clusters={cluster_file_path: {1}},
            cluster_to_files={1: {cluster_file_path}},
        )
        cluster_results = {"python": mock_cluster_result}

        agent.populate_file_methods(analysis, cluster_results)

        # Check file was assigned from cluster via CFG nodes
        file_paths = [fg.file_path for fg in sub_component.file_methods]
        self.assertIn("cluster_file.py", file_paths)


if __name__ == "__main__":
    unittest.main()
