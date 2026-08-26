import os
import tempfile
import unittest
from collections.abc import Collection
from pathlib import Path
from unittest import mock

from agents.agent_responses import (
    AnalysisInsights,
    Component,
    ComponentArchitecture,
    Relation,
    RelationEdge,
    SourceCodeReference,
)
from agents.component_ownership import ComponentOwnershipIndex
from agents.file_index_models import FileMethodGroup, MethodEntry
from agents.llm_renderers import render_scope_connections
from agents.static_analysis_enricher_mixin import StaticAnalysisEnricherMixin
from diagram_analysis.diagram_generator import DiagramGenerator, assert_scope_containment
from diagram_analysis.exceptions import ScopeContainmentError
from diagram_analysis.file_index import build_file_methods_from_nodes
from static_analyzer.cfg import CallGraph
from static_analyzer.clustering import (
    ClusterConnectionEdge,
    ClusterGroup,
    ClusterResult,
    ClusterScopeResult,
    GroupConnection,
)
from static_analyzer.analysis_result import StaticAnalysisResults
from static_analyzer.config import Language, NodeType
from static_analyzer.node import Node


def component(component_id: str, name: str) -> Component:
    return Component(name=name, description=name, key_entities=[], component_id=component_id)


def enricher(
    repo_dir: Path = Path("/repo"),
    component_ownership: ComponentOwnershipIndex = ComponentOwnershipIndex({}),
    indexed_files: Collection[str] = (),
) -> StaticAnalysisEnricherMixin:
    instance = StaticAnalysisEnricherMixin()
    instance.repo_dir = repo_dir
    instance.component_ownership = component_ownership
    graph = CallGraph(language="python")
    for index, file_path in enumerate(indexed_files, start=1):
        graph.add_node(Node(f"indexed.symbol{index}", NodeType.FUNCTION, file_path, index, index + 1))
    instance.static_analysis = StaticAnalysisResults()
    instance.static_analysis.add_cfg(Language.PYTHON, graph)
    return instance


def relation_analysis(relations: list[Relation]) -> AnalysisInsights:
    return AnalysisInsights(
        description="relations",
        components=[component("1", "A"), component("2", "B"), component("3", "C")],
        components_relations=relations,
    )


def relation_scope(*pairs: tuple[str, str]) -> ClusterScopeResult:
    symbols = {"1": "a.func", "2": "b.func", "3": "c.func"}
    graph = CallGraph(language="python")
    for index, (group_id, qualified_name) in enumerate(symbols.items(), start=1):
        graph.add_node(Node(qualified_name, NodeType.FUNCTION, f"src/{group_id}.py", index * 10, index * 10 + 5))
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


class TestStaticAnalysisEnricher(unittest.TestCase):
    def test_reconciles_llm_metadata_to_one_component_per_group(self):
        scope = ClusterScopeResult(
            scope_id="root",
            leaf_clusters_by_language={"python": ClusterResult(clusters={1: {"a"}, 2: {"b"}, 3: {"pkg.Widget"}})},
            groups=[
                ClusterGroup(group_id="2", cluster_ids=[1], symbol_members_by_language={"python": {"a"}}),
                ClusterGroup(group_id="4", cluster_ids=[2], symbol_members_by_language={"python": {"b"}}),
                ClusterGroup(
                    group_id="7",
                    cluster_ids=[3],
                    symbol_members_by_language={"python": {"pkg.Widget"}},
                ),
            ],
        )
        architecture = ComponentArchitecture(
            description="arch",
            components=[
                Component(name="Auth", description="auth", key_entities=[], source_group_names=["Group 1"]),
                Component(
                    name="Data",
                    description="data",
                    key_entities=[],
                    source_group_names=["Group 2", "Group 3"],
                ),
            ],
        )

        enricher().assemble_one_component_per_group(architecture, scope)

        self.assertEqual([item.component_id for item in architecture.components], ["2", "4", "7"])
        self.assertEqual(
            [item.source_group_names for item in architecture.components],
            [["Group 1"], ["Group 2"], ["Group 3"]],
        )
        self.assertEqual([item.source_cluster_ids for item in architecture.components], [["1"], ["2"], ["3"]])

    def test_populates_only_authoritative_group_members_across_languages(self):
        python = CallGraph(language="python")
        python.add_node(Node("main.main", NodeType.FUNCTION, "/repo/main.py", 1, 3))
        go = CallGraph(language="go")
        go.add_node(Node("main.main", NodeType.FUNCTION, "/repo/main.go", 5, 8))
        scope = ClusterScopeResult(
            scope_id="root",
            graphs_by_language={"python": python, "go": go},
            groups=[
                ClusterGroup(
                    group_id="1",
                    cluster_ids=[1],
                    symbol_members_by_language={"python": {"main.main"}},
                ),
                ClusterGroup(
                    group_id="2",
                    cluster_ids=[2],
                    symbol_members_by_language={"go": {"main.main"}},
                ),
            ],
        )
        analysis = AnalysisInsights(
            description="polyglot",
            components=[component("1", "Python"), component("2", "Go")],
            components_relations=[],
        )

        enricher().populate_file_methods(analysis, scope)

        self.assertEqual([group.file_path for group in analysis.components[0].file_methods], ["main.py"])
        self.assertEqual([group.file_path for group in analysis.components[1].file_methods], ["main.go"])

    def test_populate_file_methods_rejects_group_without_component(self):
        scope = ClusterScopeResult(scope_id="root", groups=[ClusterGroup(group_id="2", cluster_ids=[1])])
        analysis = AnalysisInsights(
            description="missing component",
            components=[component("1", "Only")],
            components_relations=[],
        )

        with self.assertRaisesRegex(RuntimeError, "Clustering group '2' has no matching component"):
            enricher().populate_file_methods(analysis, scope)

    def test_qualifies_detail_leaf_lineage(self):
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

        enricher().qualify_source_cluster_ids(analysis, "5.3")

        self.assertEqual(analysis.components[0].source_cluster_ids, ["5.3.1", "5.3.2"])


class TestFileMethodMaterialization(unittest.TestCase):
    def test_deduplicates_aliases_and_keeps_the_more_specific_name(self):
        duplicate_specific = Node(
            "diagram_analysis.diagram_generator.DiagramGenerator.generate_analysis",
            NodeType.METHOD,
            "/repo/diagram_analysis/diagram_generator.py",
            468,
            470,
        )
        duplicate_alias = Node(
            "diagram_analysis.diagram_generator.generate_analysis",
            NodeType.METHOD,
            "/repo/diagram_analysis/diagram_generator.py",
            468,
            470,
        )

        groups = build_file_methods_from_nodes([duplicate_alias, duplicate_specific], Path("/repo"))

        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0].file_path, "diagram_analysis/diagram_generator.py")
        self.assertEqual(len(groups[0].methods), 1)
        self.assertEqual(groups[0].methods[0].qualified_name, duplicate_specific.fully_qualified_name)


class TestClusterConnectionRendering(unittest.TestCase):
    def test_group_description_includes_members_assigned_outside_leaf_clusters(self):
        graph = CallGraph(language="python")
        graph.add_node(Node("pkg.clustered", NodeType.FUNCTION, "/repo/pkg.py", 1, 2))
        graph.add_node(Node("pkg.orphan", NodeType.CLASS, "/repo/pkg.py", 4, 8))
        scope = ClusterScopeResult(
            scope_id="root",
            graphs_by_language={"python": graph},
            leaf_clusters_by_language={"python": ClusterResult(clusters={1: {"pkg.clustered"}})},
            groups=[
                ClusterGroup(
                    group_id="1",
                    cluster_ids=[1],
                    symbol_members_by_language={"python": {"pkg.clustered", "pkg.orphan"}},
                )
            ],
        )

        architecture = ComponentArchitecture(description="arch", components=[])
        enricher().assemble_one_component_per_group(architecture, scope)

        self.assertIn("2 symbols", architecture.components[0].description)
        self.assertIn("pkg.orphan", architecture.components[0].description)

    def test_bounds_examples_while_counting_every_connection_edge(self):
        edges = [
            ClusterConnectionEdge(
                language="python",
                source_qualified_name=f"a.f{index}",
                target_qualified_name=f"b.f{index}",
            )
            for index in range(12)
        ]
        scope = ClusterScopeResult(
            scope_id="root",
            connections=[GroupConnection(source_group_id="1", target_group_id="2", edges=edges)],
        )

        rendered = render_scope_connections(scope, {"1": "A", "2": "B"})

        self.assertIn("A -> B (12 edges):", rendered)
        self.assertIn("... and 2 more", rendered)
        self.assertEqual(rendered.count("  f"), 10)

    def test_enriches_llm_relations_with_precomputed_scope_connections(self):
        graph = CallGraph(language="python")
        graph.add_node(Node("a.source", NodeType.FUNCTION, "/repo/a.py", 1, 3))
        graph.add_node(Node("b.target", NodeType.FUNCTION, "/repo/b.py", 5, 8))
        scope = ClusterScopeResult(
            scope_id="root",
            graphs_by_language={"python": graph},
            connections=[
                GroupConnection(
                    source_group_id="1",
                    target_group_id="2",
                    edges=[
                        ClusterConnectionEdge(
                            language="python",
                            source_qualified_name="a.source",
                            target_qualified_name="b.target",
                        )
                    ],
                )
            ],
        )
        analysis = AnalysisInsights(
            description="analysis",
            components=[component("1", "A"), component("2", "B")],
            components_relations=[],
        )

        enricher().merge_scope_relations(analysis, scope)

        self.assertEqual(len(analysis.components_relations), 1)
        relation = analysis.components_relations[0]
        self.assertEqual((relation.src_id, relation.dst_id), ("1", "2"))
        self.assertTrue(relation.is_static)
        self.assertEqual(relation.all_edges[0].source.qualified_name, "a.source")
        self.assertEqual(relation.all_edges[0].target.qualified_name, "b.target")

    def test_keeps_llm_wording_for_a_statically_backed_relation(self):
        analysis = relation_analysis([Relation(relation="depends on", src_name="A", dst_name="B")])

        enricher().merge_scope_relations(analysis, relation_scope(("1", "2")))

        self.assertEqual(len(analysis.components_relations), 1)
        relation = analysis.components_relations[0]
        self.assertEqual(relation.relation, "depends on")
        self.assertEqual(len(relation.all_edges), 1)
        self.assertTrue(relation.is_static)

    def test_statically_backed_relation_keeps_bridge_edge_locations(self):
        analysis = relation_analysis([Relation(relation="depends on", src_name="A", dst_name="B")])

        enricher().merge_scope_relations(analysis, relation_scope(("1", "2")))

        edge = analysis.components_relations[0].all_edges[0]
        self.assertEqual(edge.source.reference_file, "src/1.py")
        self.assertEqual(edge.target.reference_file, "src/2.py")

    def test_keeps_evidenced_llm_relation_without_static_backing(self):
        analysis = relation_analysis(
            [Relation(relation="uses", src_name="A", dst_name="B", evidence="Configured through plugin entry point")]
        )

        with self.assertLogs("agents.static_analysis_enricher_mixin", level="WARNING") as logs:
            enricher().merge_scope_relations(analysis, relation_scope())

        self.assertEqual(len(analysis.components_relations), 1)
        self.assertEqual(analysis.components_relations[0].evidence, "Configured through plugin entry point")
        self.assertFalse(analysis.components_relations[0].is_static)
        self.assertIn("Keeping LLM-only relation without static or key-edge backing", logs.output[0])

    def test_drops_llm_relation_without_static_backing_or_evidence(self):
        analysis = relation_analysis([Relation(relation="uses", src_name="A", dst_name="B")])

        enricher().merge_scope_relations(analysis, relation_scope())

        self.assertEqual(analysis.components_relations, [])

    def test_auto_labels_static_only_relation(self):
        analysis = relation_analysis([])

        enricher().merge_scope_relations(analysis, relation_scope(("1", "2")))

        self.assertEqual(len(analysis.components_relations), 1)
        relation = analysis.components_relations[0]
        self.assertEqual(relation.relation, "calls")
        self.assertEqual((relation.src_name, relation.dst_name), ("A", "B"))
        self.assertTrue(relation.is_static)

    def test_does_not_match_static_connection_in_reverse(self):
        analysis = relation_analysis([Relation(relation="used by", src_name="B", dst_name="A")])

        enricher().merge_scope_relations(analysis, relation_scope(("1", "2")))

        self.assertNotIn("used by", [relation.relation for relation in analysis.components_relations])
        self.assertEqual(
            [(relation.src_id, relation.dst_id) for relation in analysis.components_relations],
            [("1", "2")],
        )

    def test_merges_backed_and_static_only_connections(self):
        analysis = relation_analysis(
            [
                Relation(relation="calls", src_name="A", dst_name="B"),
                Relation(relation="uses", src_name="A", dst_name="C"),
            ]
        )

        enricher().merge_scope_relations(analysis, relation_scope(("1", "2"), ("2", "3")))

        self.assertEqual(len(analysis.components_relations), 2)
        self.assertEqual(
            {(relation.src_name, relation.dst_name) for relation in analysis.components_relations},
            {("A", "B"), ("B", "C")},
        )

    def test_empty_relations_and_connections_remain_empty(self):
        analysis = relation_analysis([])

        enricher().merge_scope_relations(analysis, relation_scope())

        self.assertEqual(analysis.components_relations, [])


class TestScopeContainment(unittest.TestCase):
    def test_rejects_a_child_holding_methods_outside_its_parent(self):
        parent = component("1", "Parent")
        parent.file_methods = [FileMethodGroup(file_path="shared.py", methods=[self._method("shared.kept", 1)])]
        child = component("1.1", "Child")
        child.file_methods = [
            FileMethodGroup(
                file_path="shared.py",
                methods=[self._method("shared.kept", 1), self._method("shared.escaped", 10)],
            )
        ]
        root = AnalysisInsights(description="root", components=[parent], components_relations=[])
        subs = {"1": AnalysisInsights(description="child", components=[child], components_relations=[])}

        with self.assertRaises(ScopeContainmentError):
            assert_scope_containment(root, subs)

    def test_rescoping_confines_children_to_the_parent(self):
        parent = component("1", "Parent")
        parent.file_methods = [FileMethodGroup(file_path="shared.py", methods=[self._method("shared.kept", 1)])]
        child = component("1.1", "Child")
        child.key_entities = [SourceCodeReference(qualified_name="shared.kept")]
        child.file_methods = [
            FileMethodGroup(
                file_path="shared.py",
                methods=[self._method("shared.kept", 1), self._method("shared.escaped", 10)],
            )
        ]
        root = AnalysisInsights(description="root", components=[parent], components_relations=[])
        subs = {"1": AnalysisInsights(description="child", components=[child], components_relations=[])}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            generator = DiagramGenerator(path, path, "repo", path, 2, "run", "log")
            generator._rescope_child_analyses(root, subs, set())

        owned = {
            method.qualified_name
            for item in subs["1"].components
            for group in item.file_methods
            for method in group.methods
        }
        self.assertEqual(owned, {"shared.kept"})
        assert_scope_containment(root, subs)

    @staticmethod
    def _method(qualified_name: str, start: int) -> MethodEntry:
        return MethodEntry(
            qualified_name=qualified_name,
            start_line=start,
            end_line=start + 5,
            node_type="FUNCTION",
            content_hash="hash",
        )


class TestRelationEdgeSourceGate(unittest.TestCase):
    """The gate a scope agent applies to its own LLM key edges, using the whole hierarchy."""

    @staticmethod
    def _hierarchy() -> ClusterScopeResult:
        """Two branches: 1 splits into 1.1/1.2, and 2 owns c.func in a scope 1 never sees."""
        branch = ClusterScopeResult(
            scope_id="1",
            groups=[
                ClusterGroup(group_id="1.1", cluster_ids=[1], symbol_members_by_language={"python": {"a.func"}}),
                ClusterGroup(group_id="1.2", cluster_ids=[2], symbol_members_by_language={"python": {"b.func"}}),
            ],
        )
        root = ClusterScopeResult(
            scope_id="root",
            groups=[
                ClusterGroup(
                    group_id="1",
                    cluster_ids=[1, 2],
                    symbol_members_by_language={"python": {"a.func", "b.func"}},
                    children=branch,
                ),
                ClusterGroup(group_id="2", cluster_ids=[3], symbol_members_by_language={"python": {"c.func"}}),
            ],
        )
        root.index_hierarchy()
        return root

    @staticmethod
    def _sub_scope() -> ClusterScopeResult:
        graph = CallGraph(language="python")
        graph.add_node(Node("a.func", NodeType.FUNCTION, "src/a.py", 1, 5))
        graph.add_node(Node("b.func", NodeType.FUNCTION, "src/b.py", 1, 5))
        return ClusterScopeResult(
            scope_id="1",
            graphs_by_language={"python": graph},
            groups=[
                ClusterGroup(group_id="1.1", cluster_ids=[1], symbol_members_by_language={"python": {"a.func"}}),
                ClusterGroup(group_id="1.2", cluster_ids=[2], symbol_members_by_language={"python": {"b.func"}}),
            ],
        )

    @staticmethod
    def _analysis(target_qualified_name: str) -> AnalysisInsights:
        return AnalysisInsights(
            description="analysis",
            components=[component("1.1", "A"), component("1.2", "B")],
            components_relations=[
                Relation(
                    relation="delegates to",
                    src_name="A",
                    dst_name="B",
                    evidence="wired at startup",
                    key_edges=[
                        RelationEdge(
                            source=SourceCodeReference(qualified_name="a.func", reference_file="src/a.py"),
                            target=SourceCodeReference(qualified_name=target_qualified_name, reference_file="src/c.py"),
                        )
                    ],
                )
            ],
        )

    def test_drops_a_key_edge_owned_by_a_component_outside_this_scope(self):
        analysis = self._analysis("c.func")
        ownership = ComponentOwnershipIndex.from_clustering_hierarchy(self._hierarchy())

        enricher(component_ownership=ownership).merge_scope_relations(analysis, self._sub_scope())

        self.assertEqual(len(analysis.components_relations), 1)
        self.assertEqual(analysis.components_relations[0].all_edges, [])

    def test_scope_only_ownership_cannot_judge_that_edge(self):
        """Why the map had to change: the scope's own components leave c.func unresolved."""
        analysis = self._analysis("c.func")
        scope_only = ComponentOwnershipIndex.from_node_owners({"a.func": "1.1", "b.func": "1.2"})

        enricher(component_ownership=scope_only).merge_scope_relations(analysis, self._sub_scope())

        self.assertEqual(len(analysis.components_relations[0].all_edges), 1)

    def test_keeps_a_key_edge_owned_by_a_descendant_of_the_declared_target(self):
        analysis = self._analysis("b.func")
        ownership = ComponentOwnershipIndex.from_clustering_hierarchy(self._hierarchy())

        enricher(component_ownership=ownership).merge_scope_relations(analysis, self._sub_scope())

        self.assertEqual(len(analysis.components_relations[0].all_edges), 1)
