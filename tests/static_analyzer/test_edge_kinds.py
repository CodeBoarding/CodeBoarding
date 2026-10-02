import pickle
import unittest

from constants import DEFAULT_STATIC_RELATION_LABEL
from static_analyzer.cfg import (
    AFFINE_REFERENCE_KINDS,
    DEFAULT_REFERENCE_KINDS,
    RELATION_REFERENCE_KINDS,
    CallGraph,
    EdgeKind,
    ReferenceEdge,
)
from static_analyzer.cfg.edge import normalize_call_site
from static_analyzer.config import NodeType
from static_analyzer.node import Node

WIRING_KINDS = {
    EdgeKind.DEPENDS_ON,
    EdgeKind.CALLS_HTTP,
    EdgeKind.ROUTES_TO,
    EdgeKind.USES,
    EdgeKind.REGISTERS_WITH,
    EdgeKind.FETCHES_CONFIG,
    EdgeKind.REPORTS_TO,
}


class TestEdgeKindPolicy(unittest.TestCase):
    def test_every_kind_has_a_verb_and_a_call_has_the_default_one(self) -> None:
        for kind in EdgeKind:
            self.assertTrue(kind.relation_label, kind)
        self.assertEqual(EdgeKind.CALL.relation_label, DEFAULT_STATIC_RELATION_LABEL)

    def test_only_wiring_kinds_are_drawn_as_relations(self) -> None:
        """INHERITS has a producer: drawing it would change every diagram, so no structural kind is drawn."""
        self.assertEqual(set(RELATION_REFERENCE_KINDS), WIRING_KINDS)
        self.assertNotIn(EdgeKind.CALL, RELATION_REFERENCE_KINDS)
        self.assertTrue(EdgeKind.CALL.drawn)
        for kind in (EdgeKind.CONTAINS, EdgeKind.INHERITS, EdgeKind.TYPEREF, EdgeKind.IMPORT):
            self.assertFalse(kind.drawn, kind)

    def test_a_drawn_kind_never_moves_a_file(self) -> None:
        self.assertEqual(AFFINE_REFERENCE_KINDS, {EdgeKind.INHERITS, EdgeKind.TYPEREF})
        self.assertFalse(any(kind.affine for kind in RELATION_REFERENCE_KINDS))

    def test_infrastructure_kinds_are_drawn(self) -> None:
        infrastructure = {kind for kind in EdgeKind if kind.infrastructure}
        self.assertEqual(infrastructure, {EdgeKind.REGISTERS_WITH, EdgeKind.FETCHES_CONFIG, EdgeKind.REPORTS_TO})
        self.assertTrue(all(kind.drawn for kind in infrastructure))

    def test_the_structural_fold_is_unchanged(self) -> None:
        self.assertEqual(DEFAULT_REFERENCE_KINDS, (EdgeKind.CONTAINS, EdgeKind.INHERITS))


class TestReferenceEdge(unittest.TestCase):
    def test_a_call_is_not_a_reference_edge(self) -> None:
        with self.assertRaises(ValueError):
            ReferenceEdge("a", "b", EdgeKind.CALL)

    def test_sites_are_normalised_and_never_identity(self) -> None:
        with_sites = ReferenceEdge("a", "b", EdgeKind.USES, ({"line": 3, "file": "app.yml", "column": 7},))
        self.assertEqual(with_sites.sites, ({"line": 3, "file": "app.yml", "column": 7},))
        self.assertEqual(with_sites, ReferenceEdge("a", "b", EdgeKind.USES))
        self.assertEqual(hash(with_sites), hash(ReferenceEdge("a", "b", EdgeKind.USES)))
        self.assertNotEqual(with_sites, ReferenceEdge("a", "b", EdgeKind.CALLS_HTTP))

    def test_a_pickle_written_without_sites_loads(self) -> None:
        old = object.__new__(ReferenceEdge)
        old.__dict__.update({"src": "a", "dst": "b", "kind": EdgeKind.INHERITS})
        loaded = pickle.loads(pickle.dumps(old))
        self.assertEqual((loaded.src, loaded.dst, loaded.kind, loaded.sites), ("a", "b", EdgeKind.INHERITS, ()))

    def test_visit_paths_rewrites_site_files_only(self) -> None:
        edge = ReferenceEdge("a", "b", EdgeKind.USES, ({"line": 1, "file": "app.yml"}, {"line": 2}))
        moved = edge.visit_paths(lambda path: f"/repo/{path}")
        self.assertEqual(moved.sites, ({"line": 1, "file": "/repo/app.yml"}, {"line": 2}))
        bare = ReferenceEdge("a", "b", EdgeKind.USES, ({"line": 2},))
        self.assertIs(bare.visit_paths(lambda path: f"/repo/{path}"), bare)


class TestCallSiteNormalisation(unittest.TestCase):
    def test_a_row_or_column_below_one_is_not_a_location(self) -> None:
        """One-based is the contract every consumer reads; a zero would point them a row or a column off."""
        for site in ({"line": 0}, {"line": -1, "column": 2}, {"line": 4, "column": 0}, {"line": 4, "column": -2}):
            with self.assertRaises(ValueError, msg=str(site)):
                normalize_call_site(site)

    def test_the_first_row_and_column_are_a_location(self) -> None:
        self.assertEqual(normalize_call_site({"line": 1, "column": 1}), {"line": 1, "column": 1})


class TestCallGraphKeepsSites(unittest.TestCase):
    def test_add_filter_and_visit_keep_sites(self) -> None:
        graph = CallGraph()
        graph.add_node(Node("a.f", NodeType.FUNCTION, "src/a.py", 1, 2))
        graph.add_node(Node("b.g", NodeType.FUNCTION, "src/b.py", 1, 2))
        graph.add_reference_edge(ReferenceEdge("a.f", "b.g", EdgeKind.USES, ({"line": 4, "file": "cfg/app.yml"},)))

        kept = graph.filter(lambda _: True)
        (edge,) = kept.reference_edges
        self.assertEqual(edge.sites, ({"line": 4, "file": "cfg/app.yml"},))

        kept.visit_paths(lambda path: f"/repo/{path}")
        self.assertEqual(kept.reference_edges[0].sites, ({"line": 4, "file": "/repo/cfg/app.yml"},))
        self.assertEqual(kept.nodes["a.f"].file_path, "/repo/src/a.py")
