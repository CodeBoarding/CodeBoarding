import json
import unittest
from pathlib import Path

from agents.agent_responses import (
    AnalysisInsights,
    Component,
    Relation,
    RelationEdge,
    SourceCodeReference,
    assign_component_ids,
    static_relation_label,
)
from diagram_analysis.analysis_json import _extract_analysis_recursive, from_analysis_to_json
from static_analyzer.cfg import EdgeKind

REPO = Path("/repo")


def _edge(kind: EdgeKind, source: str = "a.run", target: str = "b.load") -> RelationEdge:
    return RelationEdge(
        source=SourceCodeReference(qualified_name=source, reference_file="/repo/a.py"),
        target=SourceCodeReference(qualified_name=target, reference_file="/repo/b.py"),
        kind=kind,
    )


def _analysis(*edges: RelationEdge) -> AnalysisInsights:
    components = [
        Component(name="A", description="A", key_entities=[], component_id="1"),
        Component(name="B", description="B", key_entities=[], component_id="2"),
    ]
    relation = Relation.from_edges(
        static_relation_label(list(edges)), "A", "B", "1", "2", list(edges), True, default_label=True
    )
    analysis = AnalysisInsights(description="two boxes", components=components, components_relations=[relation])
    assign_component_ids(analysis)
    return analysis


def _round_trip(analysis: AnalysisInsights) -> tuple[dict, AnalysisInsights]:
    data = json.loads(from_analysis_to_json(analysis, [], REPO))
    return data, _extract_analysis_recursive(data, {}, {})


class TestEdgeKindsInAnalysisJson(unittest.TestCase):
    def test_a_call_edge_and_a_calls_relation_serialise_as_before(self) -> None:
        """A document with only calls in it has no new key, so every existing document stays byte-identical."""
        data, _ = _round_trip(_analysis(_edge(EdgeKind.CALL)))
        (relation,) = data["components_relations"]
        self.assertEqual(relation["relation"], "calls")
        self.assertNotIn("default_label", relation)
        self.assertNotIn("kind", relation["all_edges"][0])

    def test_a_wiring_edge_carries_its_kind_and_marks_the_default_verb(self) -> None:
        data, _ = _round_trip(_analysis(_edge(EdgeKind.ROUTES_TO)))
        (relation,) = data["components_relations"]
        self.assertEqual(relation["relation"], "routes to")
        self.assertIs(relation["default_label"], True)
        self.assertEqual(relation["all_edges"][0]["kind"], "routes_to")

    def test_kinds_and_the_default_flag_survive_a_read(self) -> None:
        _, read = _round_trip(_analysis(_edge(EdgeKind.USES), _edge(EdgeKind.CALL, "a.other", "b.other")))
        (relation,) = read.components_relations
        self.assertEqual(relation.relation, "calls")
        self.assertTrue(relation.has_default_label)
        self.assertEqual(sorted(edge.kind for edge in relation.all_edges), [EdgeKind.CALL, EdgeKind.USES])

    def test_a_document_without_the_flag_is_read_by_its_wording(self) -> None:
        data, _ = _round_trip(_analysis(_edge(EdgeKind.CALL)))
        data["components_relations"][0]["relation"] = "dispatches to"
        (relation,) = _extract_analysis_recursive(data, {}, {}).components_relations
        self.assertFalse(relation.has_default_label)
