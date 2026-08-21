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
from static_analyzer.clustering import ClusterGroup, ClusterResult, ClusterScopeResult


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
        self.assertIn("final_analysis", agent.prompts)

    def _make_agent(self):
        return AbstractionAgent(
            repo_dir=self.repo_dir,
            static_analysis=self.mock_static_analysis,
            project_name=self.project_name,
            meta_context=self.mock_meta_context,
            agent_llm=MagicMock(),
            parsing_llm=MagicMock(),
        )

    def test_run_uses_the_precomputed_groups(self):
        agent = self._make_agent()
        partition = ClusterResult(clusters={1: {"a"}, 2: {"b"}}, strategy="test")
        scope = ClusterScopeResult(
            scope_id="root",
            leaf_clusters_by_language={"python": partition},
            groups=[ClusterGroup(group_id="1", cluster_ids=[1, 2])],
        )
        expected = (
            AnalysisInsights(description="done", components=[], components_relations=[]),
            scope.leaf_clusters_by_language,
        )

        with (
            patch.object(agent, "step_final_analysis", return_value=expected[0]) as final_analysis,
            patch.object(agent, "populate_file_methods"),
            patch.object(agent, "step_api_surfaces", return_value=MagicMock()),
            patch.object(agent, "step_relation_analysis"),
            patch.object(agent.reference_resolver, "fix_source_code_reference_lines", return_value=expected[0]),
            patch("agents.abstraction_agent.index_relation_endpoints"),
            patch.object(agent, "_ensure_unique_key_entities"),
        ):
            result = agent.run(scope)

        cluster_analysis, partitions = final_analysis.call_args.args
        self.assertEqual(result, expected)
        self.assertIs(partitions, scope.leaf_clusters_by_language)
        self.assertEqual(len(cluster_analysis.cluster_components), 1)
        self.assertEqual(cluster_analysis.cluster_components[0].cluster_ids, [1, 2])

    @patch("agents.abstraction_agent.AbstractionAgent._invoke_repair_validate")
    def test_step_final_analysis(self, mock_invoke_repair_validate):
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
        mock_invoke_repair_validate.return_value = mock_response

        mock_cluster_result = ClusterResult(clusters={1: {"node1"}})
        cluster_results = {"python": mock_cluster_result}

        result = agent.step_final_analysis(cluster_analysis, cluster_results)

        self.assertEqual(result, mock_response)

    @patch("agents.abstraction_agent.AbstractionAgent._invoke_repair_validate")
    def test_step_final_analysis_pins_one_component_per_group(self, mock_invoke_repair_validate):
        """Even when the LLM merges/drops groups, the result has exactly one component per group."""
        agent = self._make_agent()

        cluster_analysis = ClusterAnalysis(
            cluster_components=[
                ClustersComponent(name="Group 1", cluster_ids=[1, 2], description="g1"),
                ClustersComponent(name="Group 2", cluster_ids=[3], description="g2"),
                ClustersComponent(name="Group 3", cluster_ids=[4, 5], description="g3"),
            ]
        )
        # LLM output: keeps Group 1, merges Group 2 + 3 into one component (drops a slot).
        mock_invoke_repair_validate.return_value = ComponentArchitecture(
            description="arch",
            components=[
                Component(name="Auth", description="auth", key_entities=[], source_group_names=["Group 1"]),
                Component(name="Data", description="data", key_entities=[], source_group_names=["Group 2", "Group 3"]),
            ],
        )

        cluster_results = {
            "python": ClusterResult(
                clusters={1: {"a"}, 2: {"b"}, 3: {"c"}, 4: {"pkg.Widget"}, 5: {"e"}},
            )
        }

        result = agent.step_final_analysis(cluster_analysis, cluster_results)

        # Exactly one component per group, each backed by exactly one group.
        self.assertEqual(len(result.components), 3)
        self.assertEqual([c.source_group_names for c in result.components], [["Group 1"], ["Group 2"], ["Group 3"]])
        # The claimed groups keep the LLM's names; the dropped one gets a deterministic fallback.
        self.assertEqual(result.components[0].name, "Auth")
        self.assertEqual(result.components[1].name, "Data")
        self.assertTrue(result.components[2].name)  # fallback derived from the group's symbols


if __name__ == "__main__":
    unittest.main()
