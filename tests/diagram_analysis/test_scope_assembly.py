import tempfile
import unittest
from pathlib import Path

from agents.agent_responses import AnalysisInsights, Component, Relation
from diagram_analysis.scope_assembly import ScopeAssembler
from static_analyzer import StaticAnalysisFatalError
from static_analyzer.cfg import CallGraph
from static_analyzer.clustering import (
    ClusterConnectionEdge,
    ClusterGroup,
    ClusterScopeResult,
    GroupConnection,
)
from static_analyzer.config import NodeType
from static_analyzer.node import Node


def _component(component_id: str, name: str) -> Component:
    return Component(name=name, description=name, key_entities=[], component_id=component_id)


def _scope(*pairs: tuple[str, str]) -> ClusterScopeResult:
    symbols = {"1": "a.run", "2": "b.load", "3": "c.save"}
    graph = CallGraph(language="python")
    for index, (group_id, qualified_name) in enumerate(symbols.items(), start=1):
        graph.add_node(Node(qualified_name, NodeType.FUNCTION, f"src/{group_id}.py", index, index + 1))
    return ClusterScopeResult(
        scope_id="root",
        graphs_by_language={"python": graph},
        groups=[
            ClusterGroup(
                group_id=group_id,
                cluster_ids=[int(group_id)],
                symbol_members_by_language={"python": {qualified_name}},
            )
            for group_id, qualified_name in symbols.items()
        ],
        connections=[
            GroupConnection(
                source_group_id=source_id,
                target_group_id=target_id,
                edges=[
                    ClusterConnectionEdge(
                        language="python",
                        source_qualified_name=symbols[source_id],
                        target_qualified_name=symbols[target_id],
                    )
                ],
            )
            for source_id, target_id in pairs
        ],
    )


class TestScopeAssembler(unittest.TestCase):
    def test_rejects_root_scope_without_component_groups(self) -> None:
        with self.assertRaisesRegex(StaticAnalysisFatalError, "No component groups found"):
            ScopeAssembler(Path("/repo")).build(ClusterScopeResult(scope_id="root"))

    def test_builds_one_component_per_authoritative_group(self) -> None:
        scope = _scope(("1", "2"))

        with tempfile.TemporaryDirectory() as directory:
            analysis = ScopeAssembler(Path(directory)).build(scope)

        self.assertEqual([component.component_id for component in analysis.components], ["1", "2", "3"])
        self.assertEqual([component.source_cluster_ids for component in analysis.components], [["1"], ["2"], ["3"]])
        self.assertEqual(
            [group.file_path for group in analysis.components[0].file_methods],
            ["src/1.py"],
        )
        self.assertEqual(
            [(relation.src_id, relation.dst_id) for relation in analysis.components_relations], [("1", "2")]
        )

    def test_populates_only_authoritative_members_across_languages(self) -> None:
        python = CallGraph(language="python")
        python.add_node(Node("main.main", NodeType.FUNCTION, "/repo/main.py", 1, 3))
        go = CallGraph(language="go")
        go.add_node(Node("main.main", NodeType.FUNCTION, "/repo/main.go", 5, 8))
        scope = ClusterScopeResult(
            scope_id="root",
            graphs_by_language={"python": python, "go": go},
            groups=[
                ClusterGroup(group_id="1", cluster_ids=[1], symbol_members_by_language={"python": {"main.main"}}),
                ClusterGroup(group_id="2", cluster_ids=[2], symbol_members_by_language={"go": {"main.main"}}),
            ],
        )
        analysis = AnalysisInsights(
            description="polyglot",
            components=[_component("1", "Python"), _component("2", "Go")],
            components_relations=[],
        )

        ScopeAssembler(Path("/repo")).populate_file_methods(analysis, scope)

        self.assertEqual([group.file_path for group in analysis.components[0].file_methods], ["main.py"])
        self.assertEqual([group.file_path for group in analysis.components[1].file_methods], ["main.go"])

    def test_rejects_group_without_component(self) -> None:
        scope = ClusterScopeResult(scope_id="root", groups=[ClusterGroup(group_id="2", cluster_ids=[1])])
        analysis = AnalysisInsights(
            description="missing component",
            components=[_component("1", "Only")],
            components_relations=[],
        )

        with self.assertRaisesRegex(RuntimeError, "Clustering group '2' has no matching component"):
            ScopeAssembler(Path("/repo")).populate_file_methods(analysis, scope)

    def test_qualifies_nested_cluster_lineage(self) -> None:
        analysis = AnalysisInsights(
            description="detail",
            components=[
                Component(
                    name="Child",
                    description="child",
                    key_entities=[],
                    component_id="5.3.1",
                    source_cluster_ids=["1", "2"],
                )
            ],
            components_relations=[],
        )

        ScopeAssembler.qualify_source_cluster_ids(analysis, "5.3")

        self.assertEqual(analysis.components[0].source_cluster_ids, ["5.3.1", "5.3.2"])

    def test_keeps_semantic_wording_for_static_relation(self) -> None:
        scope = _scope(("1", "2"))
        analysis = AnalysisInsights(
            description="relations",
            components=[_component("1", "A"), _component("2", "B"), _component("3", "C")],
            components_relations=[Relation(relation="dispatches to", src_name="A", dst_name="B")],
        )

        ScopeAssembler.merge_scope_relations(analysis, scope)

        relation = analysis.components_relations[0]
        self.assertEqual(relation.relation, "dispatches to")
        self.assertTrue(relation.is_static)
        self.assertEqual(relation.all_edges[0].source.reference_file, "src/1.py")
        self.assertEqual(relation.all_edges[0].target.reference_file, "src/2.py")

    def test_drops_ungrounded_semantic_relation_and_adds_static_connections(self) -> None:
        scope = _scope(("2", "3"))
        analysis = AnalysisInsights(
            description="relations",
            components=[_component("1", "A"), _component("2", "B"), _component("3", "C")],
            components_relations=[Relation(relation="uses", src_name="A", dst_name="B")],
        )

        ScopeAssembler.merge_scope_relations(analysis, scope)

        self.assertEqual(
            [(relation.src_id, relation.dst_id, relation.relation) for relation in analysis.components_relations],
            [("2", "3", "calls")],
        )


if __name__ == "__main__":
    unittest.main()
