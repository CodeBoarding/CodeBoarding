from typing import cast
import unittest
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch, PropertyMock

from agents.details_agent import DetailsAgent
from agents.agent_responses import (
    AnalysisInsights,
    ClusterAnalysis,
    Component,
    MetaAnalysisInsights,
    SourceCodeReference,
    ValidationInsights,
)
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
            assigned_files=["test.py", "test_utils.py"],
        )

    def tearDown(self):
        import shutil

        if hasattr(self, "temp_dir"):
            shutil.rmtree(self.temp_dir, ignore_errors=True)

    @patch("agents.agent.CodeBoardingAgent._static_initialize_llm")
    def test_init(self, mock_static_init):
        # Test initialization
        mock_static_init.return_value = (MagicMock(), "test-model")
        agent = DetailsAgent(
            repo_dir=self.repo_dir,
            static_analysis=self.mock_static_analysis,
            project_name=self.project_name,
            meta_context=self.mock_meta_context,
        )

        self.assertEqual(agent.project_name, self.project_name)
        self.assertEqual(agent.meta_context, self.mock_meta_context)
        self.assertIn("group_clusters", agent.prompts)
        self.assertIn("final_analysis", agent.prompts)
        self.assertIn("feedback", agent.prompts)

    @patch("agents.agent.CodeBoardingAgent._static_initialize_llm")
    def test_create_strict_component_subgraph(self, mock_static_init):
        # Test creating subgraph from component assigned files
        mock_static_init.return_value = (MagicMock(), "test-model")
        agent = DetailsAgent(
            repo_dir=self.repo_dir,
            static_analysis=self.mock_static_analysis,
            project_name=self.project_name,
            meta_context=self.mock_meta_context,
        )
        # Mock StaticAnalysis and CFG behavior
        abs_assigned = {str(self.repo_dir / f) for f in self.test_component.assigned_files}
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

    @patch("agents.agent.CodeBoardingAgent._static_initialize_llm")
    @patch("agents.details_agent.DetailsAgent._validation_invoke")
    def test_step_cluster_grouping(self, mock_validation_invoke, mock_static_init):
        # Test step_cluster_grouping
        mock_static_init.return_value = (MagicMock(), "test-model")
        agent = DetailsAgent(
            repo_dir=self.repo_dir,
            static_analysis=self.mock_static_analysis,
            project_name=self.project_name,
            meta_context=self.mock_meta_context,
        )
        mock_response = ClusterAnalysis(cluster_components=[])
        mock_validation_invoke.return_value = mock_response

        result = agent.step_cluster_grouping(self.test_component, "Mock CFG data", {})

        self.assertEqual(result, mock_response)
        mock_validation_invoke.assert_called_once()

    @patch("agents.agent.CodeBoardingAgent._static_initialize_llm")
    @patch("agents.details_agent.DetailsAgent._validation_invoke")
    def test_step_final_analysis(self, mock_validation_invoke, mock_static_init):
        # Test step_final_analysis
        mock_static_init.return_value = (MagicMock(), "test-model")
        agent = DetailsAgent(
            repo_dir=self.repo_dir,
            static_analysis=self.mock_static_analysis,
            project_name=self.project_name,
            meta_context=self.mock_meta_context,
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

    @patch("agents.agent.CodeBoardingAgent._static_initialize_llm")
    @patch("agents.details_agent.DetailsAgent._parse_invoke")
    @patch("agents.details_agent.DetailsAgent.fix_source_code_reference_lines")
    def test_run(self, mock_fix_ref, mock_parse_invoke, mock_static_init):
        # Test run method with subgraph + grouping + final analysis
        mock_static_init.return_value = (MagicMock(), "test-model")
        agent = DetailsAgent(
            repo_dir=self.repo_dir,
            static_analysis=self.mock_static_analysis,
            project_name=self.project_name,
            meta_context=self.mock_meta_context,
        )
        # Mock StaticAnalysis and CFG behavior for run
        abs_assigned = {str(self.repo_dir / f) for f in self.test_component.assigned_files}
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

        self.mock_static_analysis.get_languages.return_value = ["python"]
        self.mock_static_analysis.get_cfg.return_value = mock_cfg

        # Mock responses for grouping and final analysis
        cluster_response = ClusterAnalysis(cluster_components=[])
        final_response = AnalysisInsights(
            description="Final",
            components=[],
            components_relations=[],
        )

        mock_parse_invoke.side_effect = [cluster_response, final_response]
        mock_fix_ref.return_value = final_response

        analysis, subgraph_results = agent.run(self.test_component)

        self.assertEqual(analysis, final_response)
        self.assertEqual(mock_parse_invoke.call_count, 2)
        mock_fix_ref.assert_called_once()

    @patch("agents.agent.CodeBoardingAgent._static_initialize_llm")
    @patch("agents.cluster_methods_mixin.ClusterMethodsMixin._get_files_for_clusters")
    @patch("os.path.exists")
    @patch("os.path.relpath")
    def test_classify_files(self, mock_relpath, mock_exists, mock_get_files_for_clusters, mock_static_init):
        # Test classify_files (assigns files from clusters + key_entities)
        mock_static_init.return_value = (MagicMock(), "test-model")
        mock_get_files_for_clusters.return_value = {str(self.repo_dir / "cluster_file.py")}

        agent = DetailsAgent(
            repo_dir=self.repo_dir,
            static_analysis=self.mock_static_analysis,
            project_name=self.project_name,
            meta_context=self.mock_meta_context,
        )

        key_entity = SourceCodeReference(
            qualified_name="test.TestClass",
            reference_file=str(self.repo_dir / "test_file.py"),
            reference_start_line=1,
            reference_end_line=10,
        )

        sub_component = Component(
            name="SubComponent",
            description="Sub component",
            key_entities=[key_entity],
            source_cluster_ids=[1],
        )

        analysis = AnalysisInsights(
            description="Test analysis",
            components=[sub_component],
            components_relations=[],
        )

        mock_exists.return_value = True
        mock_relpath.side_effect = lambda path, start: Path(path).name

        # Create mock cluster_results for subgraph
        from static_analyzer.graph import ClusterResult

        mock_cluster_result = ClusterResult(
            clusters={1: {"node1"}},
            file_to_clusters={str(self.repo_dir / "cluster_file.py"): {1}},
            cluster_to_files={1: {str(self.repo_dir / "cluster_file.py")}},
        )
        cluster_results = {"python": mock_cluster_result}

        agent.classify_files(analysis, cluster_results)

        # Check files were assigned from both clusters and key_entities
        self.assertIn("cluster_file.py", sub_component.assigned_files)
        self.assertIn("test_file.py", sub_component.assigned_files)


if __name__ == "__main__":
    unittest.main()
