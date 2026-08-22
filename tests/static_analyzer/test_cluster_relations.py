"""Tests for static relation construction and relation-edge reconciliation."""

import unittest
from collections.abc import Callable

from agents.agent_responses import (
    AnalysisInsights,
    Component,
    Relation,
    RelationEdge,
    SourceCodeReference,
    assign_component_ids,
)
from agents.component_ownership import ComponentOwnershipIndex
from agents.file_index_models import FileMethodGroup, MethodEntry
from agents.relation_edges import (
    drop_reverse_duplicates,
    edge_crosses_components,
    ground_relation_edges,
    prune_ungrounded_edges,
)
from static_analyzer.cluster_relations import (
    build_component_relations,
    is_self_or_descendant,
)
from static_analyzer.config import NodeType
from static_analyzer.cfg import CallGraph, Edge
from static_analyzer.node import Node


def _make_node(name: str, file_path: str = "src/file.py", line_start: int = 1, line_end: int = 10) -> Node:
    return Node(name, NodeType.FUNCTION, file_path, line_start, line_end)


def _make_edge(src_name: str, dst_name: str, src_file: str = "src/file.py", dst_file: str = "src/file.py") -> Edge:
    return Edge(_make_node(src_name, src_file, 1, 10), _make_node(dst_name, dst_file, 20, 30), [])


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


def _owner_of(nodes: dict[str, str]) -> Callable[[SourceCodeReference], str]:
    return ComponentOwnershipIndex.from_node_owners(nodes).owner_of


class TestAnalysisNodeOwners(unittest.TestCase):

    def test_basic_mapping(self):
        analysis = AnalysisInsights(
            description="test",
            components=[
                _make_component("A", [("mod.func1", "src/a.py"), ("mod.func2", "src/a.py")], component_id="1"),
                _make_component("B", [("mod.func3", "src/b.py")], component_id="2"),
            ],
            components_relations=[],
        )
        mapping = analysis.node_owners()
        self.assertEqual(mapping["mod.func1"], "1")
        self.assertEqual(mapping["mod.func2"], "1")
        self.assertEqual(mapping["mod.func3"], "2")

    def test_empty_analysis(self):
        analysis = AnalysisInsights(description="test", components=[], components_relations=[])
        mapping = analysis.node_owners()
        self.assertEqual(mapping, {})

    def test_component_with_no_methods(self):
        comp = Component(name="Empty", description="no methods", key_entities=[], component_id="1", file_methods=[])
        analysis = AnalysisInsights(description="test", components=[comp], components_relations=[])
        mapping = analysis.node_owners()
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
        self.assertEqual(len(relations[0].all_edges), 1)
        self.assertEqual(relations[0].all_edges[0].source.qualified_name, "a.func1")
        self.assertEqual(relations[0].all_edges[0].target.qualified_name, "b.func1")

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
        self.assertEqual(len(relations[0].all_edges), 3)

    def test_bridge_edges_include_all_cross_component_edges(self):
        edge_total = 55
        node_to_comp = {f"a.f{i}": "1" for i in range(edge_total)}
        node_to_comp.update({f"b.f{i}": "2" for i in range(edge_total)})
        edges = [_make_edge(f"a.f{i}", f"b.f{i}") for i in range(edge_total)]
        cfg = CallGraph(edges=edges)
        relations = build_component_relations(node_to_comp, {"python": cfg})

        self.assertEqual(len(relations[0].all_edges), edge_total)

    def test_bridge_edges_include_locations(self):
        node_to_comp = {"a.func": "1", "b.func": "2"}
        cfg = CallGraph(edges=[_make_edge("a.func", "b.func", "src/a.py", "src/b.py")])

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
        cfg_py = CallGraph(edges=[_make_edge("py.func", "ts.func")])
        cfg_ts = CallGraph(edges=[_make_edge("ts.func", "py.other")])
        relations = build_component_relations(node_to_comp, {"python": cfg_py, "typescript": cfg_ts})

        # Should have 2 relations: 1->2 and 2->1
        src_dst_pairs = {(r.src_cluster_id, r.dst_cluster_id) for r in relations}
        self.assertIn(("1", "2"), src_dst_pairs)
        self.assertIn(("2", "1"), src_dst_pairs)

    def test_empty_graph(self):
        """Empty graph should produce no relations."""
        relations = build_component_relations({}, {"python": CallGraph()})
        self.assertEqual(len(relations), 0)


class TestAssignComponentIdsIntegration(unittest.TestCase):

    def test_ids_work_with_analysis_node_owners(self):
        analysis = AnalysisInsights(
            description="test",
            components=[
                _make_component("A", [("a.func", "src/a.py")]),
                _make_component("B", [("b.func", "src/b.py")]),
            ],
            components_relations=[],
        )
        assign_component_ids(analysis)
        mapping = analysis.node_owners()

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


def _edge(source: str, target: str, description: str = "", file: str = "src/pkg/mod.py", lines=(1, 5)) -> RelationEdge:
    return RelationEdge(
        source=SourceCodeReference(
            qualified_name=source, reference_file=file, reference_start_line=lines[0], reference_end_line=lines[1]
        ),
        target=SourceCodeReference(
            qualified_name=target, reference_file=file, reference_start_line=lines[0], reference_end_line=lines[1]
        ),
        description=description,
    )


class TestGroundRelationEdges(unittest.TestCase):
    def test_all_edges_is_the_static_set_and_llm_only_edges_are_dropped(self):
        static = [_edge("src.pkg.a.run", "src.pkg.b.load")]
        llm = [
            _edge("src.pkg.a.run", "src.pkg.b.load", description="real"),
            _edge("src.pkg.a.run", "src.pkg.c.invented", description="hallucinated"),
        ]
        key_edges, all_edges = ground_relation_edges(llm, static)
        # all_edges is exactly the deterministic CFG set; the invented edge never enters it.
        self.assertEqual(
            [(e.source.qualified_name, e.target.qualified_name) for e in all_edges],
            [("src.pkg.a.run", "src.pkg.b.load")],
        )
        self.assertEqual(
            [(e.source.qualified_name, e.target.qualified_name) for e in key_edges],
            [("src.pkg.a.run", "src.pkg.b.load")],
        )
        self.assertEqual(key_edges[0].description, "real")
        self.assertTrue(all(e.identity() in {s.identity() for s in all_edges} for e in key_edges))

    def test_non_canonical_llm_spelling_still_grounds(self):
        # A ':' class separator and a missing 'src.' prefix both denote the same symbol.
        static = [_edge("src.pkg.types.File.convert", "src.pkg.utils.open_file")]
        llm = [_edge("pkg.types.File:convert", "utils.open_file", description="opens")]
        key_edges, all_edges = ground_relation_edges(llm, static)
        self.assertEqual(len(all_edges), 1)
        self.assertEqual([e.description for e in key_edges], ["opens"])
        self.assertEqual(key_edges[0].source.qualified_name, "src.pkg.types.File.convert")

    def test_runtime_relation_without_static_keeps_llm_edges(self):
        llm = [_edge("svc.queue.publish", "worker.consume", description="via queue")]
        key_edges, all_edges = ground_relation_edges(llm, [])
        self.assertEqual(key_edges, all_edges)
        self.assertEqual([e.description for e in all_edges], ["via queue"])

    def test_static_set_is_deduplicated(self):
        dup = _edge("src.a.f", "src.b.g")
        key_edges, all_edges = ground_relation_edges([], [dup, dup])
        self.assertEqual(len(all_edges), 1)
        self.assertEqual(key_edges, [])


class TestPruneUngroundedEdges(unittest.TestCase):
    """Preservation re-injects baseline edges after the filters ran, so the assembled list is
    filtered again — otherwise an older engine's edges outlive every update that leaves the
    methods they name alone."""

    NODES = {"pkg.a.caller": "1", "pkg.a.helper": "1", "pkg.b.callee": "2"}

    def _prune(self, relation, keep_edge=lambda edge: True):
        return prune_ungrounded_edges([relation], _owner_of(self.NODES), keep_edge)

    def _relation(self, edges, evidence=""):
        return Relation(
            relation="calls",
            src_name="A",
            dst_name="B",
            src_id="1",
            dst_id="2",
            evidence=evidence,
            key_edges=list(edges),
            all_edges=list(edges),
        )

    def test_an_inherited_intra_component_edge_is_dropped(self):
        # Both ends in component 1: there is no cross-component call to preserve anywhere.
        rel = self._relation([_make_relation_edge("pkg.a.caller", "pkg.a.helper")])
        self.assertEqual(self._prune(rel), [])

    def test_a_real_call_filed_under_the_wrong_pair_is_kept_not_lost(self):
        # 1 -> 2 is real, but this relation claims 2 -> 1. Dropping it would delete the only
        # record of a genuine cross-component call.
        rel = self._relation([_make_relation_edge("pkg.a.caller", "pkg.b.callee")])
        rel.src_id, rel.dst_id = "2", "1"
        kept = self._prune(rel)
        self.assertEqual([e.target.qualified_name for e in kept[0].all_edges], ["pkg.b.callee"])

    def test_a_misfiled_call_moves_to_the_relation_that_declares_its_pair(self):
        wrong = self._relation([_make_relation_edge("pkg.a.caller", "pkg.b.callee")], evidence="e")
        wrong.src_id, wrong.dst_id = "2", "1"
        right = self._relation([])
        right.evidence = "the pair this call actually runs between"
        kept = prune_ungrounded_edges([wrong, right], _owner_of(self.NODES), lambda edge: True)
        by_pair = {(r.src_id, r.dst_id): r for r in kept}
        self.assertEqual([e.target.qualified_name for e in by_pair[("1", "2")].all_edges], ["pkg.b.callee"])
        # And the emptied backwards copy goes with it: on prose alone it now states a
        # connection the grounded relation already states the right way round.
        self.assertNotIn(("2", "1"), by_pair)

    def test_an_unrelated_misfiled_call_keeps_its_baseline_pair(self):
        wrong = self._relation([_make_relation_edge("pkg.a.caller", "pkg.b.callee")], evidence="e")
        wrong.src_id, wrong.dst_id = "2", "1"
        right = self._relation([], evidence="the pair this call actually runs between")

        kept = prune_ungrounded_edges(
            [wrong, right],
            _owner_of(self.NODES),
            lambda edge: True,
            {"pkg.other.changed"},
        )

        by_pair = {(relation.src_id, relation.dst_id): relation for relation in kept}
        self.assertEqual([edge.source.qualified_name for edge in by_pair[("2", "1")].all_edges], ["pkg.a.caller"])
        self.assertEqual(by_pair[("1", "2")].all_edges, [])

    def test_dead_edges_are_dropped_even_when_their_source_is_unchanged(self):
        relation = self._relation([_make_relation_edge("pkg.a.caller", "pkg.b.callee")])

        kept = prune_ungrounded_edges([relation], _owner_of(self.NODES), lambda edge: False, {"pkg.other.changed"})

        self.assertEqual(kept, [])

    def test_an_inherited_edge_naming_a_dead_symbol_is_dropped(self):
        rel = self._relation([_make_relation_edge("pkg.a.caller", "pkg.b.callee")])
        self.assertEqual(self._prune(rel, keep_edge=lambda edge: False), [])

    def test_a_crossing_edge_survives(self):
        rel = self._relation([_make_relation_edge("pkg.a.caller", "pkg.b.callee")])
        kept = self._prune(rel)
        self.assertEqual([e.target.qualified_name for e in kept[0].all_edges], ["pkg.b.callee"])

    def test_an_edgeless_relation_whose_reverse_is_grounded_is_dropped(self):
        # The same connection stated backwards: `B -> A` survives on prose while `A -> B` holds
        # the actual call. Keeping both draws the dependency twice, one arrow pointing the
        # wrong way, and every run that repairs it reads as churn.
        grounded = self._relation([_make_relation_edge("pkg.a.caller", "pkg.b.callee")])
        backwards = self._relation([], evidence="reads the parsed result")
        backwards.src_id, backwards.dst_id = "2", "1"
        kept = prune_ungrounded_edges([grounded, backwards], _owner_of(self.NODES), lambda edge: True)
        self.assertEqual([(r.src_id, r.dst_id) for r in kept], [("1", "2")])

    def test_an_unrelated_reverse_relation_is_not_repaired(self):
        grounded = self._relation([_make_relation_edge("pkg.a.caller", "pkg.b.callee")])
        backwards = self._relation([], evidence="reads the parsed result")
        backwards.src_id, backwards.dst_id = "2", "1"

        kept = prune_ungrounded_edges(
            [grounded, backwards], _owner_of(self.NODES), lambda edge: True, {"pkg.other.changed"}
        )

        self.assertEqual(sorted((relation.src_id, relation.dst_id) for relation in kept), [("1", "2"), ("2", "1")])

    def test_an_edgeless_relation_with_no_grounded_reverse_survives(self):
        # Nothing else states this connection, so its evidence is the only record of it.
        backwards = self._relation([], evidence="over the work queue")
        backwards.src_id, backwards.dst_id = "2", "1"
        kept = prune_ungrounded_edges([backwards], _owner_of(self.NODES), lambda edge: True)
        self.assertEqual([(r.src_id, r.dst_id) for r in kept], [("2", "1")])

    def test_a_static_edgeless_relation_is_never_dropped_for_its_reverse(self):
        grounded = self._relation([_make_relation_edge("pkg.a.caller", "pkg.b.callee")])
        backwards = self._relation([], evidence="derived from the call graph")
        backwards.src_id, backwards.dst_id = "2", "1"
        backwards.is_static = True
        kept = prune_ungrounded_edges([grounded, backwards], _owner_of(self.NODES), lambda edge: True)
        self.assertEqual(sorted((r.src_id, r.dst_id) for r in kept), [("1", "2"), ("2", "1")])

    def test_a_relation_left_edgeless_survives_on_its_evidence(self):
        # An edgeless runtime/config relation is a claim in its own right.
        rel = self._relation([_make_relation_edge("pkg.a.caller", "pkg.a.helper")], evidence="over the queue")
        kept = self._prune(rel)
        self.assertEqual([r.relation for r in kept], ["calls"])
        self.assertEqual(kept[0].all_edges, [])


class TestEdgeCrossesComponents(unittest.TestCase):
    """A runtime/config pair has no static edge to ground against, so its LLM edges are checked here."""

    NODES = {
        "scripts.engine_adapter.run_render": "1",
        "scripts.build_component_files.render_component_files": "2",
        "scripts.submit_feedback.resolve_command": "4",
        "scripts.build_cta.main": "5",
        "scripts.build_cta.build_cta": "5",
    }

    def _crosses(self, edge, src_id, dst_id):
        return edge_crosses_components(edge, _owner_of(self.NODES), src_id, dst_id)

    def test_intra_component_edge_is_not_a_cross_component_call(self):
        # Both endpoints belong to component 5, so this cannot back a 5 -> 2 relation.
        edge = _make_relation_edge("scripts.build_cta.main", "scripts.build_cta.build_cta")
        self.assertFalse(self._crosses(edge, "5", "2"))

    def test_edge_crossing_the_declared_pair_is_kept(self):
        edge = _make_relation_edge("scripts.build_cta.main", "scripts.submit_feedback.resolve_command")
        self.assertTrue(self._crosses(edge, "5", "4"))

    def test_unowned_endpoint_is_left_to_the_symbol_check(self):
        # An endpoint no component owns may be external code; validity is not decided here.
        edge = _make_relation_edge("scripts.build_cta.main", "requests.post")
        self.assertTrue(self._crosses(edge, "5", "9"))

    def test_child_component_satisfies_its_ancestor_id(self):
        edge = _make_relation_edge("scripts.build_cta.main", "scripts.submit_feedback.resolve_command")
        owner_of = _owner_of({**self.NODES, "scripts.build_cta.main": "5.1"})
        self.assertTrue(edge_crosses_components(edge, owner_of, "5", "4"))

    def test_unresolved_pair_ids_skip_the_ownership_check(self):
        edge = _make_relation_edge("scripts.build_cta.main", "scripts.submit_feedback.resolve_command")
        self.assertTrue(self._crosses(edge, "", ""))


class TestDropReverseDuplicates(unittest.TestCase):
    """Two relations between the same components, one each way, saying the same thing."""

    @staticmethod
    def _rel(src, dst, evidence="", edges=0, static=False):
        return Relation(
            relation="calls",
            src_name=src,
            dst_name=dst,
            src_id=src,
            dst_id=dst,
            evidence=evidence,
            is_static=static,
            all_edges=[_make_relation_edge(f"pkg.a{i}", f"pkg.b{i}") for i in range(edges)],
        )

    def test_the_grounded_direction_wins(self):
        kept = drop_reverse_duplicates([self._rel("1", "2", edges=1), self._rel("2", "1", evidence="e")])
        self.assertEqual([(r.src_id, r.dst_id) for r in kept], [("1", "2")])

    def test_two_bare_directions_collapse_to_one_deterministically(self):
        # Nothing distinguishes them, so the choice must not depend on run order — otherwise one
        # run's baseline carries both and the next reports an add or a delete.
        pair = [self._rel("1", "2", evidence="aa"), self._rel("2", "1", evidence="aa")]
        first = [(r.src_id, r.dst_id) for r in drop_reverse_duplicates(pair)]
        second = [(r.src_id, r.dst_id) for r in drop_reverse_duplicates(list(reversed(pair)))]
        self.assertEqual(len(first), 1)
        self.assertEqual(first, second, "the survivor must not depend on input order")

    def test_the_better_evidenced_bare_direction_is_the_one_kept(self):
        kept = drop_reverse_duplicates(
            [self._rel("1", "2", evidence="a"), self._rel("2", "1", evidence="a much longer why")]
        )
        self.assertEqual([(r.src_id, r.dst_id) for r in kept], [("2", "1")])

    def test_both_directions_survive_when_both_are_grounded(self):
        kept = drop_reverse_duplicates([self._rel("1", "2", edges=1), self._rel("2", "1", edges=1)])
        self.assertEqual(sorted((r.src_id, r.dst_id) for r in kept), [("1", "2"), ("2", "1")])

    def test_a_static_relation_is_never_collapsed(self):
        kept = drop_reverse_duplicates([self._rel("1", "2", edges=1), self._rel("2", "1", static=True)])
        self.assertEqual(sorted((r.src_id, r.dst_id) for r in kept), [("1", "2"), ("2", "1")])
