"""Tests for static_analyzer.cluster_relations module."""

import unittest

from agents.agent_responses import (
    AnalysisInsights,
    Component,
    FileMethodGroup,
    MethodEntry,
    Relation,
    assign_component_ids,
)
from static_analyzer.cluster_relations import (
    ClusterRelation,
    build_node_to_component_map,
    build_component_relations,
    merge_relations,
)
from static_analyzer.constants import NodeType
from static_analyzer.graph import CallGraph, Edge
from static_analyzer.node import Node


def _make_node(name: str, file_path: str = "src/file.py") -> Node:
    return Node(name, NodeType.FUNCTION, file_path, 1, 10)


def _make_edge(src_name: str, dst_name: str) -> Edge:
    return Edge(_make_node(src_name), _make_node(dst_name))


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

    def test_cross_component_edges(self):
        """Edges between different components should produce relations."""
        node_to_comp = {"a.func1": "1", "a.func2": "1", "b.func1": "2"}
        cfg = CallGraph(edges=[_make_edge("a.func1", "b.func1")])
        relations = build_component_relations(node_to_comp, {"python": cfg})

        self.assertEqual(len(relations), 1)
        self.assertEqual(relations[0].src_cluster_id, "1")
        self.assertEqual(relations[0].dst_cluster_id, "2")
        self.assertEqual(relations[0].edge_count, 1)

    def test_no_self_relations(self):
        """Edges within the same component should not create relations."""
        node_to_comp = {"a.func1": "1", "a.func2": "1"}
        cfg = CallGraph(edges=[_make_edge("a.func1", "a.func2")])
        relations = build_component_relations(node_to_comp, {"python": cfg})

        self.assertEqual(len(relations), 0)

    def test_unmapped_nodes_skipped(self):
        """Edges with unmapped src or dst should be skipped."""
        node_to_comp = {"a.func1": "1"}
        cfg = CallGraph(edges=[_make_edge("a.func1", "unknown.func")])
        relations = build_component_relations(node_to_comp, {"python": cfg})

        self.assertEqual(len(relations), 0)

    def test_multiple_edges_aggregated(self):
        """Multiple edges between same component pair should be aggregated."""
        node_to_comp = {"a.f1": "1", "a.f2": "1", "b.f1": "2", "b.f2": "2"}
        cfg = CallGraph(
            edges=[
                _make_edge("a.f1", "b.f1"),
                _make_edge("a.f2", "b.f2"),
                _make_edge("a.f1", "b.f2"),
            ]
        )
        relations = build_component_relations(node_to_comp, {"python": cfg})

        self.assertEqual(len(relations), 1)
        self.assertEqual(relations[0].edge_count, 3)
        self.assertEqual(len(relations[0].sample_edges), 3)

    def test_sample_edges_capped(self):
        """Sample edges should be capped at max_samples."""
        node_to_comp = {f"a.f{i}": "1" for i in range(10)}
        node_to_comp.update({f"b.f{i}": "2" for i in range(10)})
        edges = [_make_edge(f"a.f{i}", f"b.f{i}") for i in range(10)]
        cfg = CallGraph(edges=edges)
        relations = build_component_relations(node_to_comp, {"python": cfg}, max_samples=3)

        self.assertEqual(relations[0].edge_count, 10)
        self.assertEqual(len(relations[0].sample_edges), 3)

    def test_multiple_languages(self):
        """Edges across multiple language CFGs should be collected."""
        node_to_comp = {"py.func": "1", "ts.func": "2", "py.other": "1"}
        cfg_py = CallGraph(edges=[_make_edge("py.func", "ts.func")])
        cfg_ts = CallGraph(edges=[_make_edge("ts.func", "py.other")])
        relations = build_component_relations(node_to_comp, {"python": cfg_py, "typescript": cfg_ts})

        # Should have 2 relations: 1→2 and 2→1
        src_dst_pairs = {(r.src_cluster_id, r.dst_cluster_id) for r in relations}
        self.assertIn(("1", "2"), src_dst_pairs)
        self.assertIn(("2", "1"), src_dst_pairs)

    def test_empty_graph(self):
        """Empty graph should produce no relations."""
        relations = build_component_relations({}, {"python": CallGraph()})
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
        static_rels = [ClusterRelation(src_cluster_id="1", dst_cluster_id="2", edge_count=5)]

        merged = merge_relations(llm_rels, static_rels, analysis)

        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0].relation, "depends on")
        self.assertEqual(merged[0].edge_count, 5)
        self.assertTrue(merged[0].is_static)

    def test_llm_without_static_backing_dropped(self):
        """LLM relation with no static evidence should be dropped."""
        analysis = self._make_analysis()
        llm_rels = [Relation(relation="uses", src_name="A", dst_name="B")]
        static_rels = []  # No static evidence

        merged = merge_relations(llm_rels, static_rels, analysis)

        self.assertEqual(len(merged), 0)

    def test_static_only_auto_labeled(self):
        """Static relation without LLM label should get auto-label 'calls'."""
        analysis = self._make_analysis()
        llm_rels = []
        static_rels = [ClusterRelation(src_cluster_id="1", dst_cluster_id="2", edge_count=8)]

        merged = merge_relations(llm_rels, static_rels, analysis)

        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0].relation, "calls")
        self.assertEqual(merged[0].src_name, "A")
        self.assertEqual(merged[0].dst_name, "B")
        self.assertEqual(merged[0].edge_count, 8)
        self.assertTrue(merged[0].is_static)

    def test_bidirectional_matching(self):
        """LLM relation should match static relation even if direction is reversed."""
        analysis = self._make_analysis()
        llm_rels = [Relation(relation="used by", src_name="B", dst_name="A")]
        static_rels = [ClusterRelation(src_cluster_id="1", dst_cluster_id="2", edge_count=3)]

        merged = merge_relations(llm_rels, static_rels, analysis)

        # The LLM relation B→A should match static 1→2 via reverse lookup
        matching = [r for r in merged if r.relation == "used by"]
        self.assertEqual(len(matching), 1)

    def test_mixed_scenario(self):
        """Test a mix of backed, unbacked, and static-only relations."""
        analysis = self._make_analysis()
        llm_rels = [
            Relation(relation="calls", src_name="A", dst_name="B"),  # backed
            Relation(relation="uses", src_name="A", dst_name="C"),  # unbacked
        ]
        static_rels = [
            ClusterRelation(src_cluster_id="1", dst_cluster_id="2", edge_count=5),  # matches A→B
            ClusterRelation(src_cluster_id="2", dst_cluster_id="3", edge_count=2),  # static-only
        ]

        merged = merge_relations(llm_rels, static_rels, analysis)

        # A→B (backed) + B→C (static-only) = 2 relations, A→C dropped
        self.assertEqual(len(merged), 2)
        labels = {r.relation for r in merged}
        self.assertIn("calls", labels)  # both A→B and B→C have "calls"
        src_dst = {(r.src_name, r.dst_name) for r in merged}
        self.assertIn(("A", "B"), src_dst)
        self.assertIn(("B", "C"), src_dst)
        self.assertNotIn(("A", "C"), src_dst)

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
        cfg = CallGraph(
            edges=[
                _make_edge("sub1.func", "other.func"),
                _make_edge("sub1.func", "sub2.func"),
            ]
        )
        relations = build_component_relations(node_to_comp, {"python": cfg})

        # sub1→other crosses boundary (1.1→2), sub1→sub2 are different sub-components
        src_dst = {(r.src_cluster_id, r.dst_cluster_id) for r in relations}
        self.assertIn(("1.1", "2"), src_dst)
        self.assertIn(("1.1", "1.2"), src_dst)  # These ARE different component IDs
