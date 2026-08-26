import shutil
import unittest
from pathlib import Path
from unittest.mock import ANY, MagicMock, patch

from agents.details_agent import DetailsAgent
from agents.component_ownership import ComponentOwnershipIndex
from agents.agent_responses import (
    AnalysisInsights,
    Component,
    ComponentApiSurfaces,
    ComponentRelations,
    MetaAnalysisInsights,
    SourceCodeReference,
)
from agents.file_index_models import FileMethodGroup, MethodEntry
from agents.static_analysis_enricher_mixin import StaticAnalysisEnricherMixin

from diagram_analysis.file_index import build_files_index
from static_analyzer.analysis_result import StaticAnalysisResults
from static_analyzer.config import NodeType
from static_analyzer.cfg import CallGraph
from static_analyzer.clustering import (
    ClusterGroup,
    ClusterResult,
    ClusterScopeResult,
)
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
            component_ownership=ComponentOwnershipIndex({}),
        )

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
            component_ownership=ComponentOwnershipIndex({}),
        )

        self.assertEqual(agent.project_name, self.project_name)
        self.assertEqual(agent.meta_context, self.mock_meta_context)
        self.assertIn("final_analysis", agent.prompts)

    def test_run_uses_precomputed_scope(self):
        agent = self._make_agent()
        partition = ClusterResult(clusters={1: {"test.func"}}, strategy="test")
        cfg = CallGraph(language="python")
        cfg.add_node(Node("test.func", NodeType.FUNCTION, "test.py", 1, 10))
        scope = ClusterScopeResult(
            scope_id="1",
            graphs_by_language={"python": cfg},
            leaf_clusters_by_language={"python": partition},
            groups=[ClusterGroup(group_id="1.1", cluster_ids=[1])],
        )
        expected = (
            AnalysisInsights(description="done", components=[], components_relations=[]),
            scope.leaf_clusters_by_language,
        )

        with (
            patch.object(agent, "_step_llm_analysis", return_value=expected[0]) as llm_analysis,
            patch.object(agent, "populate_file_methods") as populate_methods,
            patch.object(agent, "_step_api_surfaces", return_value=MagicMock()),
            patch.object(agent, "_step_relation_analysis"),
            patch.object(agent.reference_resolver, "fix_source_code_reference_lines", return_value=expected[0]),
            patch("agents.details_agent.index_relation_endpoints"),
            patch("agents.details_agent.ensure_unique_key_entities"),
        ):
            result = agent.run(scope, self.test_component)

        self.assertEqual(result, expected)
        _component, received_scope = llm_analysis.call_args.args
        self.assertIs(received_scope, scope)
        populate_methods.assert_called_once_with(expected[0], scope)
        self.mock_static_analysis.get_cfg.assert_not_called()

    @patch("agents.details_agent.DetailsAgent._invoke_repair_validate")
    def test_llm_analysis(self, mock_invoke_repair_validate):
        mock_llm = MagicMock()
        mock_parsing_llm = MagicMock()
        agent = DetailsAgent(
            repo_dir=self.repo_dir,
            static_analysis=self.mock_static_analysis,
            project_name=self.project_name,
            meta_context=self.mock_meta_context,
            agent_llm=mock_llm,
            parsing_llm=mock_parsing_llm,
            component_ownership=ComponentOwnershipIndex({}),
        )
        mock_response = AnalysisInsights(
            description="Structure analysis",
            components=[],
            components_relations=[],
        )
        mock_invoke_repair_validate.return_value = mock_response

        scope = ClusterScopeResult(scope_id="1")
        result = agent._step_llm_analysis(self.test_component, scope)

        self.assertEqual(result, mock_response)
        mock_invoke_repair_validate.assert_called_once()

    def test_qualifies_detail_cluster_ids_with_parent_component_id(self):
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

        StaticAnalysisEnricherMixin.qualify_source_cluster_ids(analysis, "5.3")

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
            component_ownership=ComponentOwnershipIndex({}),
        )
        self.test_component.component_id = "1"
        sub_cluster_result = ClusterResult(clusters={1: {"n1"}}, strategy="test")
        subgraph = CallGraph(language="python")
        subgraph.add_node(Node("n1", NodeType.FUNCTION, str(self.repo_dir / "src" / "main.py"), 1, 10))
        scope = ClusterScopeResult(
            scope_id="1",
            graphs_by_language={"python": subgraph},
            leaf_clusters_by_language={"python": sub_cluster_result},
            groups=[
                ClusterGroup(
                    group_id="1.1",
                    cluster_ids=[1],
                    symbol_members_by_language={"python": {"n1"}},
                )
            ],
        )

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

        analysis, _subgraph_results = agent.run(scope, self.test_component)

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
        self.assertEqual(agent.toolkit.context.group_ids_by_name, {"Group 1": "1.1"})

    def test_populate_file_methods(self):
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
        scope = ClusterScopeResult(
            scope_id="root",
            graphs_by_language={"python": call_graph},
            leaf_clusters_by_language={"python": cluster_result},
            groups=[
                ClusterGroup(
                    group_id="1",
                    cluster_ids=[1],
                    symbol_members_by_language={"python": {"pkg.cluster_fn", "pkg.TestClass"}},
                )
            ],
        )

        agent = self._make_agent()
        agent.populate_file_methods(analysis, scope)

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
