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

    def test_init(self):
        # Test initialization
        mock_llm = MagicMock()
        mock_parsing_llm = MagicMock()
        agent = AbstractionAgent(
            repo_dir=self.repo_dir,
            static_analysis=self.mock_static_analysis,
            project_name=self.project_name,
            meta_context=self.mock_meta_context,
            agent_llm=mock_llm,
            parsing_llm=mock_parsing_llm,
        )

        self.assertEqual(agent.project_name, self.project_name)
        self.assertEqual(agent.meta_context, self.mock_meta_context)
        self.assertIn("group_clusters", agent.prompts)
        self.assertIn("final_analysis", agent.prompts)

    @patch("agents.abstraction_agent.AbstractionAgent._validation_invoke")
    def test_step_clusters_grouping_single_language(self, mock_validation_invoke):
        # Test step_clusters_grouping with single language
        mock_llm = MagicMock()
        mock_parsing_llm = MagicMock()
        agent = AbstractionAgent(
            repo_dir=self.repo_dir,
            static_analysis=self.mock_static_analysis,
            project_name=self.project_name,
            meta_context=self.mock_meta_context,
            agent_llm=mock_llm,
            parsing_llm=mock_parsing_llm,
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

    @patch("agents.abstraction_agent.AbstractionAgent._validation_invoke")
    def test_step_clusters_grouping_multiple_languages(self, mock_validation_invoke):
        # Test step_clusters_grouping with multiple languages
        self.mock_static_analysis.get_languages.return_value = ["python", "javascript"]

        mock_llm = MagicMock()
        mock_parsing_llm = MagicMock()
        agent = AbstractionAgent(
            repo_dir=self.repo_dir,
            static_analysis=self.mock_static_analysis,
            project_name=self.project_name,
            meta_context=self.mock_meta_context,
            agent_llm=mock_llm,
            parsing_llm=mock_parsing_llm,
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

    @patch("agents.abstraction_agent.AbstractionAgent._validation_invoke")
    def test_step_clusters_grouping_no_languages(self, mock_validation_invoke):
        # Test step_clusters_grouping with no languages detected
        self.mock_static_analysis.get_languages.return_value = []

        mock_llm = MagicMock()
        mock_parsing_llm = MagicMock()
        agent = AbstractionAgent(
            repo_dir=self.repo_dir,
            static_analysis=self.mock_static_analysis,
            project_name=self.project_name,
            meta_context=self.mock_meta_context,
            agent_llm=mock_llm,
            parsing_llm=mock_parsing_llm,
        )

        mock_response = ClusterAnalysis(
            cluster_components=[],
        )
        mock_validation_invoke.return_value = mock_response

        # Empty cluster_results for no languages
        cluster_results: dict = {}

        result = agent.step_clusters_grouping(cluster_results)

        self.assertEqual(result, mock_response)

    @patch("agents.abstraction_agent.AbstractionAgent._validation_invoke")
    def test_step_final_analysis(self, mock_validation_invoke):
        # Test step_final_analysis
        mock_llm = MagicMock()
        mock_parsing_llm = MagicMock()
        agent = AbstractionAgent(
            repo_dir=self.repo_dir,
            static_analysis=self.mock_static_analysis,
            project_name=self.project_name,
            meta_context=self.mock_meta_context,
            agent_llm=mock_llm,
            parsing_llm=mock_parsing_llm,
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


if __name__ == "__main__":
    unittest.main()
