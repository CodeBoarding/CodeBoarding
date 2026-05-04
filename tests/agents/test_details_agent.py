import shutil
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from agents.details_agent import DetailsAgent
from agents.agent_responses import (
    AnalysisInsights,
    ClusterAnalysis,
    ClustersComponent,
    Component,
    FileMethodGroup,
    MetaAnalysisInsights,
    MethodEntry,
    SourceCodeReference,
)

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
        mock_subgraph.to_cluster_string.return_value = "Component CFG String"

        mock_cfg = MagicMock()
        mock_cfg.filter_by_nodes.return_value = mock_subgraph

        self.mock_static_analysis.get_languages.return_value = ["python"]
        self.mock_static_analysis.get_cfg.return_value = mock_cfg

        subgraph_str, subgraph_cluster_results, subgraph_cfgs = agent._create_strict_component_subgraph(
            self.test_component
        )

        self.assertIn("Component CFG String", subgraph_str)
        self.assertIn("python", subgraph_cfgs)
        self.assertIs(subgraph_cfgs["python"], mock_subgraph)
        self.mock_static_analysis.get_cfg.assert_called_with("python")
        mock_cfg.filter_by_nodes.assert_called_with(expected_qnames)
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

        mock_node = MagicMock()
        mock_node.file_path = str(self.repo_dir / "src" / "main.py")
        mock_node.fully_qualified_name = "n1"
        mock_node.type = NodeType.FUNCTION
        mock_node.line_start = 1
        mock_node.line_end = 10

        mock_subgraph = MagicMock()
        mock_subgraph.nodes = {"n1": mock_node}
        mock_subgraph.cluster.return_value = mock_sub_cluster_result
        mock_subgraph.to_cluster_string.return_value = "Component CFG String"

        mock_cfg = MagicMock()
        mock_cfg.cluster.return_value = mock_cluster_result
        mock_cfg.filter_by_nodes.return_value = mock_subgraph
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
            source_cluster_ids=[1],
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

        files_index = agent.build_files_index(analysis)

        self.assertIn("shared.py", files_index)
        self.assertEqual(
            [method.qualified_name for method in files_index["shared.py"].methods],
            ["pkg.shared.alpha", "pkg.shared.beta"],
        )


class TestStepClustersGroupingWithArbiter(unittest.TestCase):
    """Gate-then-arbitrate-then-LLM-then-merge behaviour in ``step_clusters_grouping``.

    These exercise the incremental path where ``prior_index`` is populated.
    The ``prior_index is None`` path is covered by the existing
    ``test_step_clusters_grouping`` test above.
    """

    def setUp(self) -> None:
        import tempfile

        from agents.agent_responses import NameDecision
        from agents.name_arbiter import NameArbiterAgent
        from agents.prior_index_builder import build_prior_index

        self.NameDecision = NameDecision
        self.NameArbiterAgent = NameArbiterAgent
        self.build_prior_index = build_prior_index

        self.temp_dir = tempfile.mkdtemp()
        self.repo_dir = Path(self.temp_dir) / "repo"
        self.repo_dir.mkdir(parents=True, exist_ok=True)

        self.mock_static_analysis = MagicMock(spec=StaticAnalysisResults)
        self.mock_static_analysis.get_languages.return_value = ["python"]
        self.mock_static_analysis.get_cfg.return_value = MagicMock()

        self.meta_context = MetaAnalysisInsights(
            project_type="library",
            domain="software development",
            architectural_patterns=[],
            expected_components=[],
            technology_stack=["Python"],
            architectural_bias="",
        )

        self.component = Component(
            name="HostComponent",
            description="host",
            key_entities=[],
            component_id="1",
        )

        # Prior tree: one component "Authentication" with three methods.
        prior_comp = Component(
            name="Authentication",
            description="auth",
            key_entities=[],
            component_id="1.1",
            file_methods=[
                FileMethodGroup(
                    file_path="auth.py",
                    methods=[
                        MethodEntry(qualified_name=q, start_line=1, end_line=1, node_type="FUNCTION")
                        for q in ("auth.login", "auth.logout", "auth.verify_token")
                    ],
                )
            ],
        )
        prior_root = AnalysisInsights(
            description="prior",
            components=[prior_comp],
            components_relations=[],
        )
        self.prior_index = self.build_prior_index(prior_root, {})

    def tearDown(self) -> None:
        if hasattr(self, "temp_dir"):
            shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _make_agent(self) -> DetailsAgent:
        with patch("agents.agent.create_agent") as mock_create_agent:
            mock_create_agent.return_value = MagicMock()
            return DetailsAgent(
                repo_dir=self.repo_dir,
                static_analysis=self.mock_static_analysis,
                project_name="proj",
                meta_context=self.meta_context,
                agent_llm=MagicMock(),
                parsing_llm=MagicMock(),
                run_id="test-run",
            )

    def _wire_arbiter(self, agent: DetailsAgent, decision_per_call: dict[str, "NameDecision"]) -> MagicMock:
        """Stub the arbiter to return the decision keyed by prior_name."""
        mock_arbiter = MagicMock()

        def _arbitrate(prior_name: str, prior_members, new_members, cache=None):
            return decision_per_call[prior_name]

        mock_arbiter.arbitrate = _arbitrate
        agent.prior_index = self.prior_index
        agent.name_arbiter = mock_arbiter
        agent.arbiter_cache = {}
        return mock_arbiter

    @patch("agents.details_agent.DetailsAgent._validation_invoke")
    def test_arbiter_handles_matched_cluster_skipping_llm(self, mock_validation_invoke) -> None:
        # All clusters near-match a prior → arbiter handles all, LLM never called.
        agent = self._make_agent()
        self._wire_arbiter(
            agent,
            {"Authentication": self.NameDecision(event="NOOP", prior_name="Authentication", rationale="ok")},
        )

        from static_analyzer.graph import ClusterResult

        cluster_results = {
            "python": ClusterResult(
                clusters={1: {"auth.login", "auth.logout", "auth.verify_token"}},
                cluster_to_files={1: {"auth.py"}},
            )
        }

        result = agent.step_clusters_grouping(self.component, cluster_results)

        self.assertEqual(len(result.cluster_components), 1)
        self.assertEqual(result.cluster_components[0].name, "Authentication")
        self.assertEqual(result.cluster_components[0].cluster_ids, [1])
        mock_validation_invoke.assert_not_called()

    @patch("agents.details_agent.DetailsAgent._validation_invoke")
    def test_unmatched_cluster_falls_through_to_llm(self, mock_validation_invoke) -> None:
        # No prior matches → arbiter never invoked, LLM names the cluster as today.
        agent = self._make_agent()
        self._wire_arbiter(agent, {})  # arbiter will fail noisily if called.

        from static_analyzer.graph import ClusterResult

        cluster_results = {
            "python": ClusterResult(
                clusters={42: {"unrelated.alpha", "unrelated.beta"}},
                cluster_to_files={42: {"unrelated.py"}},
            )
        }
        mock_validation_invoke.return_value = ClusterAnalysis(
            cluster_components=[
                ClustersComponent(name="LLM-Named", cluster_ids=[42], description="from llm"),
            ]
        )

        result = agent.step_clusters_grouping(self.component, cluster_results)

        self.assertEqual(len(result.cluster_components), 1)
        self.assertEqual(result.cluster_components[0].name, "LLM-Named")
        mock_validation_invoke.assert_called_once()

    @patch("agents.details_agent.DetailsAgent._validation_invoke")
    def test_mixed_batch_merges_arbiter_and_llm_outputs(self, mock_validation_invoke) -> None:
        agent = self._make_agent()
        self._wire_arbiter(
            agent,
            {"Authentication": self.NameDecision(event="NOOP", prior_name="Authentication", rationale="ok")},
        )

        from static_analyzer.graph import ClusterResult

        cluster_results = {
            "python": ClusterResult(
                clusters={
                    1: {"auth.login", "auth.logout", "auth.verify_token"},  # matches Authentication
                    2: {"unrelated.alpha", "unrelated.beta"},  # no match → LLM
                },
                cluster_to_files={1: {"auth.py"}, 2: {"u.py"}},
            )
        }
        mock_validation_invoke.return_value = ClusterAnalysis(
            cluster_components=[
                ClustersComponent(name="Other", cluster_ids=[2], description="from llm"),
            ]
        )

        result = agent.step_clusters_grouping(self.component, cluster_results)

        names = sorted(cc.name for cc in result.cluster_components)
        self.assertEqual(names, ["Authentication", "Other"])
        # The LLM was invoked exactly once and only saw the unmatched cluster.
        mock_validation_invoke.assert_called_once()
        invoked_context = mock_validation_invoke.call_args.kwargs.get("context")
        self.assertEqual(invoked_context.expected_cluster_ids, {2})

    @patch("agents.details_agent.DetailsAgent._validation_invoke")
    def test_multiple_clusters_matching_same_prior_collapse_into_one_component(self, mock_validation_invoke) -> None:
        # When two new clusters both NOOP to the same prior name, they merge
        # into ONE ClustersComponent with cluster_ids=[both]. This preserves
        # the grouping the LLM would have produced for the same input.
        agent = self._make_agent()
        self._wire_arbiter(
            agent,
            {"Authentication": self.NameDecision(event="NOOP", prior_name="Authentication", rationale="ok")},
        )

        from static_analyzer.graph import ClusterResult

        # Both clusters above the 0.5 Jaccard threshold against prior
        # {auth.login, auth.logout, auth.verify_token}:
        #   cluster 1: {login, logout} → 2/3 ≈ 0.67
        #   cluster 2: {login, verify_token} → 2/3 ≈ 0.67
        cluster_results = {
            "python": ClusterResult(
                clusters={
                    1: {"auth.login", "auth.logout"},
                    2: {"auth.login", "auth.verify_token"},
                },
                cluster_to_files={1: {"auth.py"}, 2: {"auth.py"}},
            )
        }

        result = agent.step_clusters_grouping(self.component, cluster_results)

        self.assertEqual(len(result.cluster_components), 1)
        cc = result.cluster_components[0]
        self.assertEqual(cc.name, "Authentication")
        self.assertEqual(sorted(cc.cluster_ids), [1, 2])
        mock_validation_invoke.assert_not_called()

    @patch("agents.details_agent.DetailsAgent._validation_invoke")
    def test_no_prior_index_uses_full_legacy_flow(self, mock_validation_invoke) -> None:
        # prior_index is None → behaviour identical to today: every cluster
        # goes through the LLM grouping call, no arbiter involved.
        agent = self._make_agent()
        agent.prior_index = None
        agent.name_arbiter = None

        from static_analyzer.graph import ClusterResult

        cluster_results = {
            "python": ClusterResult(
                clusters={1: {"auth.login", "auth.logout"}},
                cluster_to_files={1: {"auth.py"}},
            )
        }
        mock_validation_invoke.return_value = ClusterAnalysis(
            cluster_components=[
                ClustersComponent(name="Whatever", cluster_ids=[1], description="legacy"),
            ]
        )

        result = agent.step_clusters_grouping(self.component, cluster_results)

        self.assertEqual(result.cluster_components[0].name, "Whatever")
        mock_validation_invoke.assert_called_once()
        invoked_context = mock_validation_invoke.call_args.kwargs.get("context")
        self.assertEqual(invoked_context.expected_cluster_ids, {1})


if __name__ == "__main__":
    unittest.main()
