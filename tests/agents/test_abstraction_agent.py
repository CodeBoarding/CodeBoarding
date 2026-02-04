import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from agents.abstraction_agent import AbstractionAgent
from agents.agent_responses import (
    AnalysisInsights,
    ClusterAnalysis,
    Component,
    MetaAnalysisInsights,
    SourceCodeReference,
)
from static_analyzer.analysis_result import StaticAnalysisResults
from static_analyzer.graph import ClusterResult


class TestAbstractionAgent(unittest.TestCase):
    def setUp(self):
        # Create mock static analysis
        self.mock_static_analysis = MagicMock(spec=StaticAnalysisResults)
        self.mock_static_analysis.get_languages.return_value = ["python"]
        self.mock_static_analysis.get_all_source_files.return_value = [
            Path("test_file.py"),
            Path("another_file.py"),
        ]

        # Create mock CFG
        mock_cfg = MagicMock()
        mock_cfg.to_cluster_string.return_value = "Mock CFG string"
        self.mock_static_analysis.get_cfg.return_value = mock_cfg

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

    def tearDown(self):
        import shutil

        if hasattr(self, "temp_dir"):
            shutil.rmtree(self.temp_dir, ignore_errors=True)

    @patch("agents.agent.CodeBoardingAgent._static_initialize_llm")
    def test_init(self, mock_static_init):
        # Test initialization
        mock_static_init.return_value = (MagicMock(), "test-model")
        agent = AbstractionAgent(
            repo_dir=self.repo_dir,
            static_analysis=self.mock_static_analysis,
            project_name=self.project_name,
            meta_context=self.mock_meta_context,
        )

        self.assertEqual(agent.project_name, self.project_name)
        self.assertEqual(agent.meta_context, self.mock_meta_context)
        self.assertIn("group_clusters", agent.prompts)
        self.assertIn("final_analysis", agent.prompts)

    @patch("agents.agent.CodeBoardingAgent._static_initialize_llm")
    @patch("agents.abstraction_agent.AbstractionAgent._validation_invoke")
    def test_step_clusters_grouping_single_language(self, mock_validation_invoke, mock_static_init):
        # Test step_clusters_grouping with single language
        mock_static_init.return_value = (MagicMock(), "test-model")
        agent = AbstractionAgent(
            repo_dir=self.repo_dir,
            static_analysis=self.mock_static_analysis,
            project_name=self.project_name,
            meta_context=self.mock_meta_context,
        )

        mock_response = ClusterAnalysis(
            cluster_components=[],
        )
        mock_validation_invoke.return_value = mock_response

        mock_cluster_result = ClusterResult(clusters={1: {"node1"}})
        cluster_results = {"python": mock_cluster_result}

        result = agent.step_clusters_grouping(cluster_results)

        self.assertEqual(result, mock_response)
        mock_validation_invoke.assert_called_once()

    @patch("agents.agent.CodeBoardingAgent._static_initialize_llm")
    @patch("agents.abstraction_agent.AbstractionAgent._validation_invoke")
    def test_step_clusters_grouping_multiple_languages(self, mock_validation_invoke, mock_static_init):
        # Test step_clusters_grouping with multiple languages
        mock_static_init.return_value = (MagicMock(), "test-model")
        self.mock_static_analysis.get_languages.return_value = ["python", "javascript"]

        agent = AbstractionAgent(
            repo_dir=self.repo_dir,
            static_analysis=self.mock_static_analysis,
            project_name=self.project_name,
            meta_context=self.mock_meta_context,
        )

        mock_response = ClusterAnalysis(
            cluster_components=[],
        )
        mock_validation_invoke.return_value = mock_response

        # Create mock cluster_results for both languages
        from static_analyzer.graph import ClusterResult

        mock_cluster_result = ClusterResult(clusters={1: {"node1"}})
        cluster_results = {"python": mock_cluster_result, "javascript": mock_cluster_result}

        result = agent.step_clusters_grouping(cluster_results)

        self.assertEqual(result, mock_response)
        self.mock_static_analysis.get_cfg.assert_called()

    @patch("agents.agent.CodeBoardingAgent._static_initialize_llm")
    @patch("agents.abstraction_agent.AbstractionAgent._validation_invoke")
    def test_step_clusters_grouping_no_languages(self, mock_validation_invoke, mock_static_init):
        # Test step_clusters_grouping with no languages detected
        mock_static_init.return_value = (MagicMock(), "test-model")
        self.mock_static_analysis.get_languages.return_value = []

        agent = AbstractionAgent(
            repo_dir=self.repo_dir,
            static_analysis=self.mock_static_analysis,
            project_name=self.project_name,
            meta_context=self.mock_meta_context,
        )

        mock_response = ClusterAnalysis(
            cluster_components=[],
        )
        mock_validation_invoke.return_value = mock_response

        # Empty cluster_results for no languages
        cluster_results: dict = {}

        result = agent.step_clusters_grouping(cluster_results)

        self.assertEqual(result, mock_response)

    @patch("agents.agent.CodeBoardingAgent._static_initialize_llm")
    @patch("agents.abstraction_agent.AbstractionAgent._validation_invoke")
    def test_step_final_analysis(self, mock_validation_invoke, mock_static_init):
        # Test step_final_analysis
        mock_static_init.return_value = (MagicMock(), "test-model")
        agent = AbstractionAgent(
            repo_dir=self.repo_dir,
            static_analysis=self.mock_static_analysis,
            project_name=self.project_name,
            meta_context=self.mock_meta_context,
        )

        cluster_analysis = ClusterAnalysis(
            cluster_components=[],
        )

        mock_response = AnalysisInsights(
            description="Final analysis",
            components=[],
            components_relations=[],
        )
        mock_validation_invoke.return_value = mock_response

        # Create mock cluster_results
        from static_analyzer.graph import ClusterResult

        mock_cluster_result = ClusterResult(clusters={1: {"node1"}})
        cluster_results = {"python": mock_cluster_result}

        result = agent.step_final_analysis(cluster_analysis, cluster_results)

        self.assertEqual(result, mock_response)

    @patch("agents.agent.CodeBoardingAgent._classify_unassigned_files_with_llm")
    @patch("agents.agent.CodeBoardingAgent._static_initialize_llm")
    @patch("agents.cluster_methods_mixin.ClusterMethodsMixin._get_files_for_clusters")
    @patch("os.path.exists")
    @patch("os.path.relpath")
    def test_classify_files(
        self, mock_relpath, mock_exists, mock_get_files_for_clusters, mock_static_init, mock_classify_unassigned
    ):
        # Test classify_files (assigns files from clusters + key_entities)
        mock_static_init.return_value = (MagicMock(), "test-model")
        mock_get_files_for_clusters.return_value = {str(self.repo_dir / "cluster_file.py")}
        mock_classify_unassigned.return_value = []  # Mock LLM classification to return empty list

        agent = AbstractionAgent(
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

        component = Component(
            name="TestComponent",
            description="Test component",
            key_entities=[key_entity],
            source_cluster_ids=[1, 2],
        )

        analysis = AnalysisInsights(
            description="Test analysis",
            components=[component],
            components_relations=[],
        )

        mock_exists.return_value = True
        mock_relpath.side_effect = lambda path, start: Path(path).name

        # Create mock cluster_results
        from static_analyzer.graph import ClusterResult

        mock_cluster_result = ClusterResult(
            clusters={1: {"node1"}, 2: {"node2"}},
            file_to_clusters={str(self.repo_dir / "cluster_file.py"): {1, 2}},
            cluster_to_files={1: {str(self.repo_dir / "cluster_file.py")}, 2: {str(self.repo_dir / "cluster_file.py")}},
        )
        cluster_results = {"python": mock_cluster_result}

        scope_files = [str(self.repo_dir / "cluster_file.py"), str(self.repo_dir / "test_file.py")]
        agent.classify_files(analysis, cluster_results, scope_files)

        # Check files were assigned from both clusters and key_entities
        self.assertIn("cluster_file.py", component.assigned_files)
        self.assertIn("test_file.py", component.assigned_files)


if __name__ == "__main__":
    unittest.main()
