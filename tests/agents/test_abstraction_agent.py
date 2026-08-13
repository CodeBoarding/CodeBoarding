import shutil
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from agents.abstraction_agent import AbstractionAgent
from agents.agent_responses import (
    AnalysisInsights,
    ClusterAnalysis,
    ClustersComponent,
    Component,
    ComponentArchitecture,
    MetaAnalysisInsights,
)
from static_analyzer.analysis_result import StaticAnalysisResults
from static_analyzer.clustering.models import ClusterResult
from static_analyzer.clustering.service import ClusteringResults


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
        if hasattr(self, "temp_dir"):
            shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _make_clustering(
        self,
        cluster_analysis: ClusterAnalysis | None = None,
        cluster_results: dict[str, ClusterResult] | None = None,
    ) -> ClusteringResults:
        return ClusteringResults(
            cluster_results=cluster_results or {},
            cfg_graphs={},
            cluster_analysis=cluster_analysis or ClusterAnalysis(cluster_components=[]),
            static_analysis=self.mock_static_analysis,
        )

    def _make_agent(self, clustering: ClusteringResults | None = None) -> AbstractionAgent:
        return AbstractionAgent(
            repo_dir=self.repo_dir,
            clustering=clustering or self._make_clustering(),
            project_name=self.project_name,
            meta_context=self.mock_meta_context,
            agent_llm=MagicMock(),
            parsing_llm=MagicMock(),
        )

    def test_init(self):
        agent = self._make_agent()

        self.assertEqual(agent.project_name, self.project_name)
        self.assertEqual(agent.meta_context, self.mock_meta_context)
        self.assertIn("final_analysis", agent.prompts)

    @patch("agents.abstraction_agent.AbstractionAgent._invoke_repair_validate")
    def test_step_analysis_shell(self, mock_invoke_repair_validate):
        cluster_results = {"python": ClusterResult(clusters={1: {"node1"}})}
        agent = self._make_agent(self._make_clustering(cluster_results=cluster_results))

        mock_response = AnalysisInsights(
            description="Final analysis",
            components=[],
            components_relations=[],
        )
        mock_invoke_repair_validate.return_value = mock_response

        result = agent.step_analysis_shell()

        self.assertEqual(result, mock_response)

    @patch("agents.abstraction_agent.AbstractionAgent._invoke_repair_validate")
    def test_step_analysis_shell_pins_one_component_per_group(self, mock_invoke_repair_validate):
        """Even when the LLM merges/drops groups, the result has exactly one component per group."""
        cluster_analysis = ClusterAnalysis(
            cluster_components=[
                ClustersComponent(name="Group 1", cluster_ids=[1, 2], description="g1"),
                ClustersComponent(name="Group 2", cluster_ids=[3], description="g2"),
                ClustersComponent(name="Group 3", cluster_ids=[4, 5], description="g3"),
            ]
        )
        cluster_results = {
            "python": ClusterResult(
                clusters={1: {"a"}, 2: {"b"}, 3: {"c"}, 4: {"pkg.Widget"}, 5: {"e"}},
            )
        }
        agent = self._make_agent(self._make_clustering(cluster_analysis, cluster_results))

        # LLM output: keeps Group 1, merges Group 2 + 3 into one component (drops a slot).
        mock_invoke_repair_validate.return_value = ComponentArchitecture(
            description="arch",
            components=[
                Component(name="Auth", description="auth", key_entities=[], source_group_names=["Group 1"]),
                Component(name="Data", description="data", key_entities=[], source_group_names=["Group 2", "Group 3"]),
            ],
        )

        result = agent.step_analysis_shell()

        # Exactly one component per group, each backed by exactly one group.
        self.assertEqual(len(result.components), 3)
        self.assertEqual([c.source_group_names for c in result.components], [["Group 1"], ["Group 2"], ["Group 3"]])
        # The claimed groups keep the LLM's names; the dropped one gets a deterministic fallback.
        self.assertEqual(result.components[0].name, "Auth")
        self.assertEqual(result.components[1].name, "Data")
        self.assertTrue(result.components[2].name)  # fallback derived from the group's symbols


if __name__ == "__main__":
    unittest.main()
