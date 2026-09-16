"""A join becomes an arrow: the endpoint it lands on, the box that owns it, and the dump it grades as.

Nothing here reads the disk for its graphs. A unit is code because symbols sit inside its directory,
so a graph built in memory over a pretend repository says everything these rules depend on.
"""

import json
import tempfile
import unittest
from pathlib import Path

from clustering_ids import ROOT_SCOPE_ID
from static_analyzer.analysis_result import StaticAnalysisResults
from static_analyzer.cfg import CallGraph, EdgeKind, ReferenceEdge
from static_analyzer.clustering.models import ClusterGroup, ClusterScopeResult
from static_analyzer.config import NodeType
from static_analyzer.node import Node
from static_analyzer.wiring import run, write_dump
from static_analyzer.wiring.emit import emit, endpoint_nodes, place
from static_analyzer.wiring.join import Join
from static_analyzer.wiring_results import AnchorFamily, Unit, UnitKind

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "wiring"
REPO = Path("/repo")

API = Unit(id="api", dir="api", kind=UnitKind.NPM, manifest="api/package.json")
WEB = Unit(id="web", dir="web", kind=UnitKind.NPM, manifest="web/package.json")


def graph_of(language: str, *files: str) -> CallGraph:
    """A graph holding one callable per file, which is what makes a unit's directory code."""
    nodes = {}
    for path in files:
        name = f"{path}|run"
        nodes[name] = Node(name, NodeType.FUNCTION, str(REPO / path), 1, 2)
    return CallGraph(nodes, (), language)


def site(line: int) -> Join:
    return Join(
        source="web",
        target="api",
        kind=EdgeKind.CALLS_HTTP,
        file="docker-compose.yml",
        line=line,
        column=1,
        key="http://api:8080",
        family=AnchorFamily.DEPLOYMENT,
    )


class TestEndpoints(unittest.TestCase):
    def test_a_unit_is_reached_at_its_own_manifest(self) -> None:
        graphs = {"typescript": graph_of("typescript", "api/app.ts")}

        endpoints = endpoint_nodes([API], graphs, REPO)

        self.assertEqual(
            {unit: node.fully_qualified_name for unit, node in endpoints.items()}, {"api": "api/package.json"}
        )
        self.assertEqual(endpoints["api"].type, NodeType.FILE)

    def test_three_units_are_reached_nowhere(self) -> None:
        """Nothing to point at, nothing that makes it a box, or the whole tree (§4)."""
        units = [
            Unit(id="a", dir="a", kind=UnitKind.NPM),
            Unit(id="b", dir="b", kind=UnitKind.NPM, manifest="b/package.json"),
            Unit(id=".", dir=".", kind=UnitKind.NPM, manifest="package.json"),
        ]
        graphs = {"typescript": graph_of("typescript", "a/app.ts", "b_other/app.ts")}

        self.assertEqual(endpoint_nodes(units, graphs, REPO), {})


class TestEmit(unittest.TestCase):
    def test_two_places_declaring_one_connection_are_one_edge(self) -> None:
        """Sites say where an arrow was declared; they never make it a second arrow."""
        graphs = {"typescript": graph_of("typescript", "api/app.ts", "web/app.ts")}

        edges = emit([site(5), site(9)], [API, WEB], graphs, REPO)

        self.assertEqual(len(edges), 1)
        self.assertEqual(
            (edges[0].src, edges[0].dst, edges[0].kind), ("web/package.json", "api/package.json", EdgeKind.CALLS_HTTP)
        )
        self.assertEqual([place["line"] for place in edges[0].sites], [5, 9])

    def test_an_arrow_needs_both_of_its_ends(self) -> None:
        graphs = {"typescript": graph_of("typescript", "web/app.ts")}

        self.assertEqual(emit([site(5)], [API, WEB], graphs, REPO), [])


class TestPlace(unittest.TestCase):
    def _hierarchy(self) -> tuple[ClusterScopeResult, ClusterGroup, ClusterGroup]:
        graph = graph_of("typescript", "api/app.ts", "web/app.ts")
        api = ClusterGroup(group_id="1", cluster_ids=[1], symbol_members_by_language={"typescript": {"api/app.ts|run"}})
        web = ClusterGroup(group_id="2", cluster_ids=[2], symbol_members_by_language={"typescript": {"web/app.ts|run"}})
        return (
            ClusterScopeResult(scope_id=ROOT_SCOPE_ID, graphs_by_language={"typescript": graph}, groups=[api, web]),
            api,
            web,
        )

    def test_an_endpoint_joins_the_group_that_owns_its_directory(self) -> None:
        hierarchy, api, web = self._hierarchy()
        edges = emit([site(5)], [API, WEB], hierarchy.graphs_by_language, REPO)

        place(hierarchy, [API, WEB], edges, REPO)

        self.assertIn("api/package.json", api.symbol_members_by_language["typescript"])
        self.assertIn("web/package.json", web.symbol_members_by_language["typescript"])
        self.assertNotIn("api/package.json", web.symbol_members_by_language["typescript"])
        self.assertIn("api/package.json", hierarchy.graphs_by_language["typescript"].nodes)

    def test_an_arrow_between_two_languages_is_drawn(self) -> None:
        """The case the layer exists for: a compose file wiring a Python service to a Java one.

        An edge can only be drawn in a graph holding both its ends, so the target's node joins the
        source's graph. Keeping each end in its own graph lost every such arrow, and silently.
        """
        python = graph_of("python", "api/app.py")
        typescript = graph_of("typescript", "web/app.ts")
        api = ClusterGroup(group_id="1", cluster_ids=[1], symbol_members_by_language={"python": {"api/app.py|run"}})
        web = ClusterGroup(group_id="2", cluster_ids=[2], symbol_members_by_language={"typescript": {"web/app.ts|run"}})
        hierarchy = ClusterScopeResult(
            scope_id=ROOT_SCOPE_ID,
            graphs_by_language={"python": python, "typescript": typescript},
            groups=[api, web],
        )
        edges = emit([site(5)], [API, WEB], hierarchy.graphs_by_language, REPO)

        place(hierarchy, [API, WEB], edges, REPO)

        self.assertEqual(
            [(edge.src, edge.dst) for edge in typescript.reference_edges], [("web/package.json", "api/package.json")]
        )
        self.assertIn("api/package.json", typescript.nodes)
        self.assertIn("api/package.json", api.symbol_members_by_language["python"])

    def test_with_no_edges_nothing_is_placed(self) -> None:
        """The flag-off guarantee: a run that draws no wiring leaves every box as it was."""
        hierarchy, api, web = self._hierarchy()

        place(hierarchy, [API, WEB], [], REPO)

        self.assertEqual(api.symbol_members_by_language, {"typescript": {"api/app.ts|run"}})
        self.assertEqual(web.symbol_members_by_language, {"typescript": {"web/app.ts|run"}})


class TestEdgesDump(unittest.TestCase):
    def test_edges_are_dumped_in_the_schema_the_scorer_reads(self) -> None:
        directory = Path(self.enterContext(tempfile.TemporaryDirectory()))
        repository = FIXTURES / "join-dependencies"
        wiring = run(StaticAnalysisResults(), repository)
        wiring.edges = [
            ReferenceEdge(
                "src/Api/Api.csproj",
                "Core.csproj",
                EdgeKind.DEPENDS_ON,
                ({"file": "src/Api/Api.csproj", "line": 7, "column": 1},),
            )
        ]

        write_dump(wiring, repository, directory)
        dumped = json.loads((directory / "edges.json").read_text())

        self.assertEqual(sorted(dumped), ["commit", "edges", "repo"])
        self.assertEqual(sorted(dumped["edges"][0]), ["dst", "kind", "sites", "src"])
        self.assertEqual(dumped["edges"][0]["kind"], "depends_on")
        self.assertEqual(dumped["edges"][0]["sites"], [{"file": "src/Api/Api.csproj", "line": 7, "column": 1}])


if __name__ == "__main__":
    unittest.main()
