"""Tests for static_analyzer.cluster_relations module."""

import unittest

from agents.agent_responses import (
    AnalysisInsights,
    Component,
    Relation,
    RelationEdge,
    SourceCodeReference,
    assign_component_ids,
)
from agents.file_index_models import FileMethodGroup, MethodEntry
from static_analyzer.cluster_relations import (
    ClusterRelation,
    build_component_relation_candidates,
    build_node_to_component_map,
    build_component_relations,
    is_self_or_descendant,
    merge_relations,
)
from static_analyzer.constants import NodeType
from static_analyzer.program_graph import (
    ProgramEdge,
    ProgramEdgeKind,
    ProgramGraph,
    ProgramNode,
    ProgramNodeKind,
    file_node_id,
    package_node_id,
)


def _make_node(name: str, file_path: str = "src/file.py", line_start: int = 1, line_end: int = 10) -> ProgramNode:
    return ProgramNode(
        name,
        ProgramNodeKind.SYMBOL,
        "python",
        name.rsplit(".", 1)[-1],
        file_path,
        NodeType.FUNCTION,
        line_start,
        line_end,
    )


def _make_edge(
    src_name: str, dst_name: str, src_file: str = "src/file.py", dst_file: str = "src/file.py"
) -> ProgramEdge:
    return ProgramEdge(
        ProgramEdgeKind.CALL,
        src_name,
        dst_name,
        metadata={"source_file": src_file, "target_file": dst_file},
    )


def _make_graph(edges: list[ProgramEdge]) -> ProgramGraph:
    graph = ProgramGraph(language="python")
    files: dict[str, str] = {}
    for edge in edges:
        files[edge.source] = str(edge.metadata["source_file"])
        files[edge.target] = str(edge.metadata["target_file"])
    for index, node_id in enumerate(sorted(files)):
        line_start, line_end = (1, 10) if index == 0 else (index * 20, index * 20 + 10)
        graph.add_node(_make_node(node_id, files[node_id], line_start, line_end))
    for edge in edges:
        graph.add_edge(edge)
    return graph


def _make_relation_edge(
    src_name: str, dst_name: str, src_file: str = "src/a.py", dst_file: str = "src/b.py"
) -> RelationEdge:
    return RelationEdge(
        source=SourceCodeReference(
            qualified_name=src_name,
            reference_file=src_file,
            reference_start_line=1,
            reference_end_line=10,
        ),
        target=SourceCodeReference(
            qualified_name=dst_name,
            reference_file=dst_file,
            reference_start_line=20,
            reference_end_line=30,
        ),
        call_sites=[],
    )


def _make_component(name: str, methods: list[tuple[str, str]], component_id: str = "") -> Component:
    """Create a component with file_methods populated from (qualified_name, file_path) tuples."""
    file_groups: dict[str, list[MethodEntry]] = {}
    for qname, fpath in methods:
        if fpath not in file_groups:
            file_groups[fpath] = []
        file_groups[fpath].append(MethodEntry(qualified_name=qname, start_line=1, end_line=10, node_type="FUNCTION"))
    return Component(
        name=name,
        description=f"{name} component",
        key_entities=[],
        component_id=component_id,
        file_methods=[FileMethodGroup(file_path=fp, methods=meths) for fp, meths in file_groups.items()],
    )


class TestBuildNodeToComponentMap(unittest.TestCase):

    def test_basic_mapping(self):
        analysis = AnalysisInsights(
            description="test",
            components=[
                _make_component("A", [("mod.func1", "src/a.py"), ("mod.func2", "src/a.py")], component_id="1"),
                _make_component("B", [("mod.func3", "src/b.py")], component_id="2"),
            ],
            components_relations=[],
        )
        mapping = build_node_to_component_map(analysis)
        self.assertEqual(mapping["mod.func1"], "1")
        self.assertEqual(mapping["mod.func2"], "1")
        self.assertEqual(mapping["mod.func3"], "2")

    def test_empty_analysis(self):
        analysis = AnalysisInsights(description="test", components=[], components_relations=[])
        mapping = build_node_to_component_map(analysis)
        self.assertEqual(mapping, {})

    def test_component_with_no_methods(self):
        comp = Component(name="Empty", description="no methods", key_entities=[], component_id="1", file_methods=[])
        analysis = AnalysisInsights(description="test", components=[comp], components_relations=[])
        mapping = build_node_to_component_map(analysis)
        self.assertEqual(mapping, {})


class TestBuildComponentRelations(unittest.TestCase):

    def test_architectural_candidates_include_imports_and_inheritance_without_concrete_edges(self):
        graph = ProgramGraph(language="python")
        source_file = "src/plugin.py"
        target_init = "src/core/__init__.py"
        target_file = "src/core/service.py"
        source_file_id = file_node_id(source_file)
        target_init_id = file_node_id(target_init)
        target_file_id = file_node_id(target_file)
        source_package = package_node_id("python", "plugin")
        target_package = package_node_id("python", "core")
        for node in (
            ProgramNode(source_file_id, ProgramNodeKind.FILE, "python", source_file, source_file),
            ProgramNode(target_init_id, ProgramNodeKind.FILE, "python", target_init, target_init),
            ProgramNode(target_file_id, ProgramNodeKind.FILE, "python", target_file, target_file),
            ProgramNode(source_package, ProgramNodeKind.PACKAGE, "python", "plugin"),
            ProgramNode(target_package, ProgramNodeKind.PACKAGE, "python", "core"),
            _make_node("plugin.run", source_file),
            _make_node("plugin.Extension", source_file),
            _make_node("core.Service", target_file),
        ):
            graph.add_node(node)
        for container, member in (
            (source_package, source_file_id),
            (target_package, target_init_id),
            (target_package, target_file_id),
            (source_file_id, "plugin.run"),
            (source_file_id, "plugin.Extension"),
            (target_file_id, "core.Service"),
        ):
            graph.add_edge(ProgramEdge(ProgramEdgeKind.CONTAINS, container, member))
        graph.add_edge(ProgramEdge(ProgramEdgeKind.CALL, "plugin.run", "core.Service"))
        graph.add_edge(ProgramEdge(ProgramEdgeKind.INHERITS, "plugin.Extension", "core.Service"))
        graph.add_edge(
            ProgramEdge(
                ProgramEdgeKind.IMPORTS,
                source_file_id,
                target_init_id,
                metadata={"declared_module": "core", "imported_names": ["Service"]},
            )
        )
        owners = {"plugin.run": "1", "plugin.Extension": "1", "core.Service": "2"}

        candidates = build_component_relation_candidates(owners, {"python": graph})
        concrete = build_component_relations(owners, {"python": graph})

        self.assertEqual(len(candidates), 1)
        self.assertEqual(
            {item.kind for item in candidates[0].evidence},
            {
                ProgramEdgeKind.CALL,
                ProgramEdgeKind.IMPORTS,
                ProgramEdgeKind.INHERITS,
            },
        )
        self.assertEqual(len(concrete), 1)
        self.assertEqual(len(concrete[0].all_edges), 1)

    def test_cross_component_edges(self):
        """Edges between different components should produce relations."""
        node_to_comp = {"a.func1": "1", "a.func2": "1", "b.func1": "2"}
        cfg = _make_graph([_make_edge("a.func1", "b.func1")])
        relations = build_component_relations(node_to_comp, {"python": cfg})

        self.assertEqual(len(relations), 1)
        self.assertEqual(relations[0].src_cluster_id, "1")
        self.assertEqual(relations[0].dst_cluster_id, "2")
        self.assertEqual(len(relations[0].all_edges), 1)
        self.assertEqual(relations[0].all_edges[0].source.qualified_name, "a.func1")
        self.assertEqual(relations[0].all_edges[0].target.qualified_name, "b.func1")

    def test_no_self_relations(self):
        """Edges within the same component should not create relations."""
        node_to_comp = {"a.func1": "1", "a.func2": "1"}
        cfg = _make_graph([_make_edge("a.func1", "a.func2")])
        relations = build_component_relations(node_to_comp, {"python": cfg})

        self.assertEqual(len(relations), 0)

    def test_unmapped_nodes_skipped(self):
        """Edges with unmapped src or dst should be skipped."""
        node_to_comp = {"a.func1": "1"}
        cfg = _make_graph([_make_edge("a.func1", "unknown.func")])
        relations = build_component_relations(node_to_comp, {"python": cfg})

        self.assertEqual(len(relations), 0)

    def test_multiple_edges_aggregated(self):
        """Multiple edges between same component pair should be aggregated."""
        node_to_comp = {"a.f1": "1", "a.f2": "1", "b.f1": "2", "b.f2": "2"}
        cfg = _make_graph(
            [
                _make_edge("a.f1", "b.f1"),
                _make_edge("a.f2", "b.f2"),
                _make_edge("a.f1", "b.f2"),
            ]
        )
        relations = build_component_relations(node_to_comp, {"python": cfg})

        self.assertEqual(len(relations), 1)
        self.assertEqual(len(relations[0].all_edges), 3)

    def test_bridge_edges_include_all_cross_component_edges(self):
        edge_total = 55
        node_to_comp = {f"a.f{i}": "1" for i in range(edge_total)}
        node_to_comp.update({f"b.f{i}": "2" for i in range(edge_total)})
        edges = [_make_edge(f"a.f{i}", f"b.f{i}") for i in range(edge_total)]
        cfg = _make_graph(edges)
        relations = build_component_relations(node_to_comp, {"python": cfg})

        self.assertEqual(len(relations[0].all_edges), edge_total)

    def test_bridge_edges_include_locations(self):
        node_to_comp = {"a.func": "1", "b.func": "2"}
        cfg = _make_graph([_make_edge("a.func", "b.func", "src/a.py", "src/b.py")])

        relations = build_component_relations(node_to_comp, {"python": cfg})

        edge = relations[0].all_edges[0]
        self.assertEqual(edge.source.reference_file, "src/a.py")
        self.assertEqual(edge.target.reference_file, "src/b.py")
        self.assertEqual(edge.source.reference_start_line, 1)
        self.assertEqual(edge.source.reference_end_line, 10)
        self.assertEqual(edge.target.reference_start_line, 20)
        self.assertEqual(edge.target.reference_end_line, 30)

    def test_multiple_languages(self):
        """Edges across multiple language CFGs should be collected."""
        node_to_comp = {"py.func": "1", "ts.func": "2", "py.other": "1"}
        cfg_py = _make_graph([_make_edge("py.func", "ts.func")])
        cfg_ts = _make_graph([_make_edge("ts.func", "py.other")])
        relations = build_component_relations(node_to_comp, {"python": cfg_py, "typescript": cfg_ts})

        # Should have 2 relations: 1->2 and 2->1
        src_dst_pairs = {(r.src_cluster_id, r.dst_cluster_id) for r in relations}
        self.assertIn(("1", "2"), src_dst_pairs)
        self.assertIn(("2", "1"), src_dst_pairs)

    def test_empty_graph(self):
        """Empty graph should produce no relations."""
        relations = build_component_relations({}, {"python": ProgramGraph(language="python")})
        self.assertEqual(len(relations), 0)


class TestMergeRelations(unittest.TestCase):

    def _make_analysis(self) -> AnalysisInsights:
        analysis = AnalysisInsights(
            description="test",
            components=[
                _make_component("A", [("a.func", "src/a.py")], component_id="1"),
                _make_component("B", [("b.func", "src/b.py")], component_id="2"),
                _make_component("C", [("c.func", "src/c.py")], component_id="3"),
            ],
            components_relations=[],
        )
        return analysis

    def test_llm_with_static_backing_kept(self):
        """LLM relation backed by static evidence should be kept."""
        analysis = self._make_analysis()
        llm_rels = [Relation(relation="depends on", src_name="A", dst_name="B")]
        static_rels = [
            ClusterRelation(src_cluster_id="1", dst_cluster_id="2", all_edges=[_make_relation_edge("a.func", "b.func")])
        ]

        merged = merge_relations(llm_rels, static_rels, analysis)

        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0].relation, "depends on")
        self.assertEqual(len(merged[0].all_edges), 1)
        self.assertTrue(merged[0].is_static)

    def test_llm_with_static_backing_keeps_bridge_edges(self):
        analysis = self._make_analysis()
        llm_rels = [Relation(relation="depends on", src_name="A", dst_name="B")]
        static_rels = build_component_relations(
            {"a.func": "1", "b.func": "2"},
            {"python": _make_graph([_make_edge("a.func", "b.func", "src/a.py", "src/b.py")])},
        )

        merged = merge_relations(llm_rels, static_rels, analysis)

        self.assertEqual(len(merged[0].all_edges), 1)
        self.assertEqual(merged[0].all_edges[0].source.reference_file, "src/a.py")
        self.assertEqual(merged[0].all_edges[0].target.reference_file, "src/b.py")

    def test_llm_with_evidence_without_static_backing_kept_with_warning(self):
        analysis = self._make_analysis()
        llm_rels = [
            Relation(relation="uses", src_name="A", dst_name="B", evidence="Configured through plugin entry point")
        ]
        static_rels: list[ClusterRelation] = []  # No static evidence

        with self.assertLogs("static_analyzer.cluster_relations", level="WARNING") as logs:
            merged = merge_relations(llm_rels, static_rels, analysis)

        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0].evidence, "Configured through plugin entry point")
        self.assertFalse(merged[0].is_static)
        self.assertIn("Keeping LLM-only relation without static or key-edge backing", logs.output[0])

    def test_llm_without_static_backing_or_evidence_dropped(self):
        analysis = self._make_analysis()
        llm_rels = [Relation(relation="uses", src_name="A", dst_name="B")]

        merged = merge_relations(llm_rels, [], analysis)

        self.assertEqual(merged, [])

    def test_static_only_auto_labeled(self):
        """Static relation without LLM label should get auto-label 'calls'."""
        analysis = self._make_analysis()
        llm_rels: list[Relation] = []
        static_rels = [
            ClusterRelation(src_cluster_id="1", dst_cluster_id="2", all_edges=[_make_relation_edge("a.func", "b.func")])
        ]

        merged = merge_relations(llm_rels, static_rels, analysis)

        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0].relation, "calls")
        self.assertEqual(merged[0].src_name, "A")
        self.assertEqual(merged[0].dst_name, "B")
        self.assertEqual(len(merged[0].all_edges), 1)
        self.assertTrue(merged[0].is_static)

    def test_reverse_direction_does_not_match_static_relation(self):
        analysis = self._make_analysis()
        llm_rels = [Relation(relation="used by", src_name="B", dst_name="A")]
        static_rels = [
            ClusterRelation(src_cluster_id="1", dst_cluster_id="2", all_edges=[_make_relation_edge("a.func", "b.func")])
        ]

        merged = merge_relations(llm_rels, static_rels, analysis)

        matching = [r for r in merged if r.relation == "used by"]
        self.assertEqual(matching, [])
        static_only = [r for r in merged if r.src_id == "1" and r.dst_id == "2"]
        self.assertEqual(len(static_only), 1)
        self.assertTrue(static_only[0].is_static)

    def test_mixed_scenario(self):
        """Test a mix of backed, unbacked, and static-only relations."""
        analysis = self._make_analysis()
        llm_rels = [
            Relation(relation="calls", src_name="A", dst_name="B"),  # backed
            Relation(relation="uses", src_name="A", dst_name="C"),  # unbacked
        ]
        static_rels = [
            ClusterRelation(
                src_cluster_id="1", dst_cluster_id="2", all_edges=[_make_relation_edge("a.func", "b.func")]
            ),
            ClusterRelation(
                src_cluster_id="2", dst_cluster_id="3", all_edges=[_make_relation_edge("b.func", "c.func")]
            ),
        ]

        merged = merge_relations(llm_rels, static_rels, analysis)

        # A->B (backed) + B->C (static-only); A->C is dropped because it has no evidence
        self.assertEqual(len(merged), 2)
        src_dst = {(r.src_name, r.dst_name) for r in merged}
        self.assertIn(("A", "B"), src_dst)
        self.assertIn(("B", "C"), src_dst)

    def test_empty_inputs(self):
        """Empty LLM and static relations should produce empty result."""
        analysis = self._make_analysis()
        merged = merge_relations([], [], analysis)
        self.assertEqual(len(merged), 0)


class TestAssignComponentIdsIntegration(unittest.TestCase):
    """Integration tests for assign_component_ids with cluster_relations."""

    def test_ids_work_with_node_to_component_map(self):
        """Verify that assigned IDs work correctly with build_node_to_component_map."""
        analysis = AnalysisInsights(
            description="test",
            components=[
                _make_component("A", [("a.func", "src/a.py")]),
                _make_component("B", [("b.func", "src/b.py")]),
            ],
            components_relations=[],
        )
        assign_component_ids(analysis)
        mapping = build_node_to_component_map(analysis)

        self.assertEqual(mapping["a.func"], "1")
        self.assertEqual(mapping["b.func"], "2")

    def test_nested_ids_work_with_build_relations(self):
        """Verify that nested hierarchical IDs produce correct relations."""
        node_to_comp = {"sub1.func": "1.1", "sub2.func": "1.2", "other.func": "2"}
        cfg = _make_graph(
            [
                _make_edge("sub1.func", "other.func"),
                _make_edge("sub1.func", "sub2.func"),
            ]
        )
        relations = build_component_relations(node_to_comp, {"python": cfg})

        # sub1->other crosses boundary (1.1->2), sub1->sub2 are different sub-components
        src_dst = {(r.src_cluster_id, r.dst_cluster_id) for r in relations}
        self.assertIn(("1.1", "2"), src_dst)
        self.assertIn(("1.1", "1.2"), src_dst)  # These ARE different component IDs


class TestIsSelfOrDescendant(unittest.TestCase):

    def test_self(self):
        self.assertTrue(is_self_or_descendant("1", "1"))
        self.assertTrue(is_self_or_descendant("1.2", "1.2"))

    def test_descendant(self):
        self.assertTrue(is_self_or_descendant("1.2", "1"))
        self.assertTrue(is_self_or_descendant("1.2.3", "1"))
        self.assertTrue(is_self_or_descendant("1.2.3", "1.2"))

    def test_not_descendant(self):
        # Shared prefix but not a dotted-boundary descendant.
        self.assertFalse(is_self_or_descendant("10", "1"))
        self.assertFalse(is_self_or_descendant("1", "1.2"))
        self.assertFalse(is_self_or_descendant("2.1", "1"))
