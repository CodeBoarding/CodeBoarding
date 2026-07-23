import shutil
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import networkx as nx

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
        # Every leaf cluster is owned by exactly one group (a true partition).
        assigned = [cid for cc in result.cluster_components for cid in cc.cluster_ids]
        self.assertEqual(sorted(assigned), sorted(expected_ids))
        self.assertEqual(len(assigned), len(set(assigned)))

    def test_step_clusters_grouping_single_language(self):
        agent = self._make_agent()
        cr, graph = self._clustered_graph(range(1, 13))
        self.mock_static_analysis.get_cfg.return_value.to_networkx.return_value = graph
        self.mock_static_analysis.get_cfg.return_value.clustering_networkx.return_value = graph
        cluster_results = {"python": cr}

        result = agent.step_clusters_grouping(cluster_results)
        result_again = agent.step_clusters_grouping(cluster_results)

        self._assert_partition(result, list(range(1, 13)))
        # Deterministic: same membership on a re-run.
        self.assertEqual(
            [sorted(cc.cluster_ids) for cc in result.cluster_components],
            [sorted(cc.cluster_ids) for cc in result_again.cluster_components],
        )

    def test_step_clusters_grouping_multiple_languages(self):
        self.mock_static_analysis.get_languages.return_value = ["python", "javascript"]
        agent = self._make_agent()
        # Globally-unique cluster ids across languages, sharing one combined graph.
        _, graph = self._clustered_graph(range(1, 13))
        py_cr, _ = self._clustered_graph(range(1, 7))
        js_cr, _ = self._clustered_graph(range(7, 13))
        self.mock_static_analysis.get_cfg.return_value.to_networkx.return_value = graph
        self.mock_static_analysis.get_cfg.return_value.clustering_networkx.return_value = graph
        cluster_results = {"python": py_cr, "javascript": js_cr}

        result = agent.step_clusters_grouping(cluster_results)

        self._assert_partition(result, list(range(1, 13)))
        self.mock_static_analysis.get_cfg.assert_called()

    def test_step_clusters_grouping_no_languages(self):
        self.mock_static_analysis.get_languages.return_value = []
        agent = self._make_agent()

        result = agent.step_clusters_grouping({})

        self.assertIsInstance(result, ClusterAnalysis)
        self.assertEqual(result.cluster_components, [])

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

        # Create mock cluster_results
        from static_analyzer.graph import ClusterResult

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
