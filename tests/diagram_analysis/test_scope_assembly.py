import tempfile
import unittest
from pathlib import Path

from agents.agent_responses import AnalysisInsights, Component, Relation, RelationEdge, SourceCodeReference
from agents.scope_analysis_agent import ScopeAnalysisResult, ScopeComponentSemantics, ScopeRelationSemantics
from diagram_analysis.scope_assembly import ScopeAssembler
from static_analyzer import StaticAnalysisFatalError, StaticAnalysisResults
from static_analyzer.cfg import CallGraph
from static_analyzer.clustering import (
    ClusterConnectionEdge,
    ClusterGroup,
    ClusterScopeResult,
    GroupConnection,
)
from static_analyzer.config import Language, NodeType
from static_analyzer.node import Node
from static_analyzer.reference_resolver import StaticReferenceResolver


def _component(component_id: str, name: str) -> Component:
    return Component(name=name, description=name, key_entities=[], component_id=component_id)


def _scope(*pairs: tuple[str, str], names: dict[str, str] | None = None, absolute: bool = False) -> ClusterScopeResult:
    symbols = {"1": "a.run", "2": "b.load", "3": "c.save"}
    names = names or {}
    graph = CallGraph(language="python")
    for index, (group_id, qualified_name) in enumerate(symbols.items(), start=1):
        path = f"/repo/src/{group_id}.py" if absolute else f"src/{group_id}.py"
        graph.add_node(Node(qualified_name, NodeType.FUNCTION, path, index, index + 1))
    return ClusterScopeResult(
        scope_id="root",
        graphs_by_language={"python": graph},
        groups=[
            ClusterGroup(
                group_id=group_id,
                name=names.get(group_id, ""),
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


def _resolver(scope: ClusterScopeResult) -> StaticReferenceResolver:
    static_analysis = StaticAnalysisResults()
    static_analysis.add_cfg(Language.PYTHON, scope.graphs_by_language["python"])
    static_analysis.add_references(Language.PYTHON, list(scope.graphs_by_language["python"].nodes.values()))
    return StaticReferenceResolver(Path("/repo"), static_analysis)


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

    def test_fallback_descriptions_name_files_relative_to_the_repository(self) -> None:
        analysis = ScopeAssembler(Path("/repo")).build(_scope(absolute=True))

        self.assertEqual(analysis.components[0].description, "Owns 1 symbols across 1 files: src/1.py.")
        self.assertNotIn("/repo", analysis.components[0].description)

    def test_a_proposal_cannot_take_an_enclosing_components_name(self) -> None:
        scope = _scope(names={"1": "engine", "2": "adapters"})
        assembler = ScopeAssembler(Path("/repo"))
        analysis = assembler.build(scope)
        result = ScopeAnalysisResult(
            description="",
            components=[
                ScopeComponentSemantics(group_id="1", name="Static Analysis Engine", description="core"),
                ScopeComponentSemantics(group_id="2", name="Adapters", description="per language"),
            ],
            relations=[],
        )

        unnamed = assembler.apply_semantics(
            analysis, scope, result, {"1", "2"}, set(), _resolver(scope), reserved_names=("Static Analysis Engine",)
        )

        self.assertEqual([c.name for c in analysis.components], ["engine", "Adapters", "Component 3"])
        self.assertEqual(unnamed, frozenset({"1"}))

    def test_names_components_from_the_clustering_rule_that_claimed_them(self) -> None:
        scope = _scope(names={"1": "Ingestion", "2": "Storage"})

        analysis = ScopeAssembler(Path("/repo")).build(scope)

        self.assertEqual(
            [component.name for component in analysis.components],
            ["Ingestion", "Storage", "Component 3"],
        )

    def test_disambiguates_two_groups_claiming_the_same_rule_name(self) -> None:
        scope = _scope(names={"1": "Storage", "2": "Storage"})

        analysis = ScopeAssembler(Path("/repo")).build(scope)

        self.assertEqual(
            [component.name for component in analysis.components],
            ["Storage", "Storage 2", "Component 3"],
        )

    def test_keeps_suffixing_until_the_name_is_free(self) -> None:
        scope = _scope(names={"1": "Storage", "2": "Storage 3", "3": "Storage"})

        analysis = ScopeAssembler(Path("/repo")).build(scope)

        self.assertEqual(
            [component.name for component in analysis.components],
            ["Storage", "Storage 3", "Storage 3 2"],
        )

    def test_keeps_existing_key_entities_when_semantics_omit_them(self) -> None:
        scope = _scope()
        assembler = ScopeAssembler(Path("/repo"))
        analysis = assembler.build(scope)
        analysis.components[0].key_entities = [SourceCodeReference(qualified_name="a.run")]
        result = ScopeAnalysisResult(
            components=[ScopeComponentSemantics(group_id="1", name="Runner", description="Runs.")],
        )

        assembler.apply_semantics(analysis, scope, result, {"1"}, set(), _resolver(scope))

        self.assertEqual([entity.qualified_name for entity in analysis.components[0].key_entities], ["a.run"])

    def test_an_explicit_empty_key_entity_list_clears_them(self) -> None:
        scope = _scope()
        assembler = ScopeAssembler(Path("/repo"))
        analysis = assembler.build(scope)
        analysis.components[0].key_entities = [SourceCodeReference(qualified_name="a.run")]
        result = ScopeAnalysisResult.model_validate_json(
            '{"components": [{"group_id": "1", "name": "Runner", "description": "Runs.", "key_entities": []}]}'
        )

        assembler.apply_semantics(analysis, scope, result, {"1"}, set(), _resolver(scope))

        self.assertEqual(analysis.components[0].key_entities, [])

    def test_names_are_allocated_against_final_siblings_so_a_swap_is_valid(self) -> None:
        scope = _scope(names={"1": "API", "2": "Storage"})
        assembler = ScopeAssembler(Path("/repo"))
        analysis = assembler.build(scope)
        result = ScopeAnalysisResult(
            components=[
                ScopeComponentSemantics(group_id="1", name="Storage", description="Stores."),
                ScopeComponentSemantics(group_id="2", name="Persistence", description="Persists."),
            ],
        )

        unnamed = assembler.apply_semantics(analysis, scope, result, {"1", "2"}, set(), _resolver(scope))

        self.assertEqual([component.name for component in analysis.components[:2]], ["Storage", "Persistence"])
        self.assertEqual(unnamed, frozenset())

    def test_a_losing_duplicate_proposal_falls_back_to_its_rule_name_and_is_reported(self) -> None:
        scope = _scope(names={"1": "API", "2": "Storage", "3": "Cache"})
        assembler = ScopeAssembler(Path("/repo"))
        analysis = assembler.build(scope)
        result = ScopeAnalysisResult(
            components=[
                ScopeComponentSemantics(group_id="1", name="Storage", description="Stores."),
                ScopeComponentSemantics(group_id="2", name="Storage", description="Also stores."),
                ScopeComponentSemantics(group_id="3", name="", description="Blank name."),
            ],
        )

        unnamed = assembler.apply_semantics(analysis, scope, result, {"1", "2", "3"}, set(), _resolver(scope))

        self.assertEqual([component.name for component in analysis.components], ["Storage", "Storage 2", "Cache"])
        self.assertEqual(unnamed, frozenset({"2", "3"}))

    def test_a_proposal_cannot_take_a_name_held_by_a_group_that_cannot_move(self) -> None:
        scope = _scope(names={"1": "API", "2": "Storage"})
        assembler = ScopeAssembler(Path("/repo"))
        analysis = assembler.build(scope)
        result = ScopeAnalysisResult(
            components=[ScopeComponentSemantics(group_id="1", name="Storage", description="Stores.")],
        )

        unnamed = assembler.apply_semantics(analysis, scope, result, {"1"}, set(), _resolver(scope))

        self.assertEqual([component.name for component in analysis.components[:2]], ["API", "Storage"])
        self.assertEqual(unnamed, frozenset({"1"}))

    def test_keeps_the_previous_label_when_semantics_omit_a_still_connected_pair(self) -> None:
        scope = _scope(("1", "2"))
        assembler = ScopeAssembler(Path("/repo"))
        analysis = assembler.build(scope)
        analysis.components_relations = [
            Relation(
                relation="dispatches to",
                src_name=analysis.components[0].name,
                dst_name=analysis.components[1].name,
                src_id="1",
                dst_id="2",
                is_static=True,
            )
        ]
        result = ScopeAnalysisResult(
            components=[ScopeComponentSemantics(group_id="1", name="Runner", description="Runs.", key_entities=[])],
        )

        assembler.apply_semantics(analysis, scope, result, {"1"}, set(), _resolver(scope))

        self.assertEqual(
            [(relation.src_id, relation.dst_id, relation.relation) for relation in analysis.components_relations],
            [("1", "2", "dispatches to")],
        )

    def test_semantics_cannot_change_fixed_ids_membership_or_a_locked_name(self) -> None:
        scope = _scope(("1", "2"))
        assembler = ScopeAssembler(Path("/repo"))
        analysis = assembler.build(scope)
        analysis.components[0].name = "Stable"
        original_membership = {
            component.component_id: [
                (group.file_path, [method.qualified_name for method in group.methods])
                for group in component.file_methods
            ]
            for component in analysis.components
        }
        result = ScopeAnalysisResult(
            description="Named scope",
            components=[
                ScopeComponentSemantics(
                    group_id="1",
                    name="Renamed",
                    description="Updated responsibility.",
                    key_entities=[],
                ),
                ScopeComponentSemantics(
                    group_id="unknown",
                    name="Invented",
                    description="Must be ignored.",
                    key_entities=[],
                ),
            ],
            relations=[
                ScopeRelationSemantics(
                    source_group_id="1",
                    target_group_id="2",
                    relation="dispatches to",
                )
            ],
        )
        static_analysis = StaticAnalysisResults()
        static_analysis.add_cfg(Language.PYTHON, scope.graphs_by_language["python"])
        static_analysis.add_references(Language.PYTHON, list(scope.graphs_by_language["python"].nodes.values()))

        assembler.apply_semantics(
            analysis,
            scope,
            result,
            {"1"},
            {"1"},
            StaticReferenceResolver(Path("/repo"), static_analysis),
        )

        self.assertEqual([component.component_id for component in analysis.components], ["1", "2", "3"])
        self.assertEqual(analysis.components[0].name, "Stable")
        self.assertEqual(analysis.components[0].description, "Updated responsibility.")
        self.assertEqual(
            {
                component.component_id: [
                    (group.file_path, [method.qualified_name for method in group.methods])
                    for group in component.file_methods
                ]
                for component in analysis.components
            },
            original_membership,
        )
        self.assertEqual(analysis.components_relations[0].relation, "dispatches to")
        self.assertTrue(analysis.components_relations[0].is_static)

    def test_non_static_relation_requires_resolvable_evidence_on_both_sides(self) -> None:
        scope = _scope()
        assembler = ScopeAssembler(Path("/repo"))
        analysis = assembler.build(scope)
        result = ScopeAnalysisResult(
            components=[],
            relations=[
                ScopeRelationSemantics(
                    source_group_id="1",
                    target_group_id="2",
                    relation="posts orders to",
                    evidence="a.run posts to the endpoint handled by b.load",
                    key_edges=[
                        RelationEdge(
                            source=SourceCodeReference(qualified_name="a.run"),
                            target=SourceCodeReference(qualified_name="b.load"),
                            description="HTTP client and route handler",
                        )
                    ],
                ),
                ScopeRelationSemantics(
                    source_group_id="1",
                    target_group_id="3",
                    relation="configures",
                    evidence="name-only claim with no source symbols",
                ),
            ],
        )
        static_analysis = StaticAnalysisResults()
        static_analysis.add_cfg(Language.PYTHON, scope.graphs_by_language["python"])
        static_analysis.add_references(Language.PYTHON, list(scope.graphs_by_language["python"].nodes.values()))

        assembler.apply_semantics(
            analysis,
            scope,
            result,
            {"1"},
            set(),
            StaticReferenceResolver(Path("/repo"), static_analysis),
        )

        self.assertEqual(
            [(relation.src_id, relation.dst_id, relation.relation) for relation in analysis.components_relations],
            [("1", "2", "posts orders to")],
        )
        edge = analysis.components_relations[0].key_edges[0]
        self.assertEqual(edge.source.reference_file, "src/1.py")
        self.assertEqual(edge.target.reference_file, "src/2.py")


if __name__ == "__main__":
    unittest.main()
