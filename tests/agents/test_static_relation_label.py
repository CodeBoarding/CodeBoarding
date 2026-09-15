import unittest

from agents.agent_responses import Relation, RelationEdge, SourceCodeReference, static_relation_label
from static_analyzer.cfg import EdgeKind


def _edge(kind: EdgeKind, target: str = "b.T") -> RelationEdge:
    return RelationEdge(
        source=SourceCodeReference(qualified_name="a.S", reference_file="src/a.py"),
        target=SourceCodeReference(qualified_name=target, reference_file="src/b.py"),
        kind=kind,
    )


class TestStaticRelationLabel(unittest.TestCase):
    def test_no_edges_is_a_call(self) -> None:
        self.assertEqual(static_relation_label([]), "calls")

    def test_a_call_outranks_everything(self) -> None:
        self.assertEqual(static_relation_label([_edge(EdgeKind.USES), _edge(EdgeKind.CALL)]), "calls")

    def test_one_kind_reads_as_its_verb(self) -> None:
        self.assertEqual(static_relation_label([_edge(EdgeKind.REGISTERS_WITH)]), "registers with")

    def test_the_most_frequent_kind_wins(self) -> None:
        edges = [_edge(EdgeKind.USES), _edge(EdgeKind.USES, "b.U"), _edge(EdgeKind.CALLS_HTTP, "b.V")]
        self.assertEqual(static_relation_label(edges), "uses")

    def test_a_tie_goes_to_the_kind_declared_first(self) -> None:
        self.assertEqual(
            static_relation_label([_edge(EdgeKind.USES), _edge(EdgeKind.CALLS_HTTP, "b.U")]), "calls over HTTP"
        )


class TestDefaultLabel(unittest.TestCase):
    def test_a_relation_built_from_edges_knows_its_label_is_the_default(self) -> None:
        edges = [_edge(EdgeKind.USES)]
        relation = Relation.from_edges(
            static_relation_label(edges), "A", "B", "1", "2", edges, True, default_label=True
        )
        self.assertEqual(relation.relation, "uses")
        self.assertTrue(relation.has_default_label)
        self.assertTrue(relation.with_merged_edges().has_default_label)

    def test_an_unmarked_relation_is_judged_by_its_wording(self) -> None:
        self.assertTrue(Relation(relation="calls", src_name="A", dst_name="B").has_default_label)
        self.assertFalse(Relation(relation="uses", src_name="A", dst_name="B").has_default_label)
        self.assertFalse(Relation(relation="calls", src_name="A", dst_name="B", default_label=False).has_default_label)

    def test_from_dict_reads_a_kind_and_defaults_to_a_call(self) -> None:
        row = {"source": "src/a.py|a.S", "target": "src/b.py|b.T"}
        self.assertIs(RelationEdge.from_dict(row, {}).kind, EdgeKind.CALL)
        self.assertIs(RelationEdge.from_dict({**row, "kind": "routes_to"}, {}).kind, EdgeKind.ROUTES_TO)
        with self.assertRaises(ValueError):
            RelationEdge.from_dict({**row, "kind": "teleports"}, {})
