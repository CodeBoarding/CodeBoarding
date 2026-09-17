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
from static_analyzer.cfg import CallSiteLocation, Edge, EdgeKind
from static_analyzer.config import NodeType
from static_analyzer.node import Node

REPO = Path("/repo")
SOURCE_NODE = Node("a.run", NodeType.FUNCTION, "/repo/a.py", 1, 2)
TARGET_NODE = Node("b.load", NodeType.FUNCTION, "/repo/b.py", 1, 2)


def _edge(kind: EdgeKind, source: str = "a.run", target: str = "b.load") -> RelationEdge:
    return RelationEdge(
        source=SourceCodeReference(qualified_name=source, reference_file="/repo/a.py"),
        target=SourceCodeReference(qualified_name=target, reference_file="/repo/b.py"),
        kind=kind,
    )


def _wiring_edge(*sites: CallSiteLocation) -> RelationEdge:
    return RelationEdge.from_reference(SOURCE_NODE, TARGET_NODE, EdgeKind.USES, sites)


def _call_edge(*sites: CallSiteLocation) -> RelationEdge:
    return RelationEdge.from_edge(Edge(SOURCE_NODE, TARGET_NODE, sites))


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

    def test_an_authored_calls_relation_keeps_its_false(self) -> None:
        """Unflagged, the reader's wording fallback turns an authored `calls` back into a static default."""
        analysis = _analysis(_edge(EdgeKind.CALL))
        analysis.components_relations[0].default_label = False

        data, read = _round_trip(analysis)

        self.assertIs(data["components_relations"][0]["default_label"], False)
        self.assertFalse(read.components_relations[0].has_default_label)


class TestCallSitesInAnalysisJson(unittest.TestCase):
    def test_a_wiring_site_reaches_the_document_repo_relative_and_with_a_column(self) -> None:
        data, _ = _round_trip(_analysis(_wiring_edge({"line": 4, "file": "/repo/ops/compose.yml"})))
        (site,) = data["components_relations"][0]["all_edges"][0]["call_sites"]
        self.assertEqual(site, {"line": 4, "column": 1, "file": "ops/compose.yml"})

    def test_a_call_site_is_written_with_no_file_key(self) -> None:
        """A document of call edges alone has no new key, so every existing document stays byte-identical."""
        data, _ = _round_trip(_analysis(_call_edge({"line": 12, "column": 8, "file": "/repo/a.py"})))
        (site,) = data["components_relations"][0]["all_edges"][0]["call_sites"]
        self.assertEqual(site, {"line": 12, "column": 8})
