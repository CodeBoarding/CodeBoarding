"""A join becomes an arrow: the endpoint it lands on, the box that owns it, and the dump it grades as.

Nothing here reads the disk for its graphs. A unit is code because symbols sit inside its directory,
so a graph built in memory over a pretend repository says everything these rules depend on.
"""

import json
import tempfile
import unittest
from pathlib import Path

from agents.agent_responses import AnalysisInsights, RelationEdge
from clustering_ids import ROOT_SCOPE_ID
from diagram_analysis.analysis_json import build_unified_analysis_json
from diagram_analysis.file_index import index_artifact_files
from diagram_analysis.scope_assembly import ScopeAssembler
from static_analyzer.analysis_result import StaticAnalysisResults
from static_analyzer.cfg import CallGraph, EdgeKind, ReferenceEdge
from static_analyzer.cluster_relations import (
    build_component_relations,
    build_global_node_to_component_map,
    build_global_relations,
)
from static_analyzer.clustering.models import ClusterGroup, ClusterScopeResult
from static_analyzer.clustering.service import hierarchy_differs
from static_analyzer.config import NodeType
from static_analyzer.node import Node
from static_analyzer.wiring import run, write_dump
from static_analyzer.wiring.emit import WIRING_GRAPH, common_ancestor, emit, endpoint_nodes, place
from static_analyzer.wiring.join import Join
from static_analyzer.wiring_results import AnchorFamily, DiagnosticCode, Resource, ResourceKind, Unit, UnitKind

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "wiring"
REPO = Path("/repo")

API = Unit(id="api", dir="api", kind=UnitKind.NPM, manifest="api/package.json")
WEB = Unit(id="web", dir="web", kind=UnitKind.NPM, manifest="web/package.json")
REDIS = Resource(
    key="resource:cache:redis",
    kind=ResourceKind.CACHE,
    name="redis",
    display_name="Redis",
    users=("web",),
    home_unit="web",
)
POSTGRES = Resource(
    key="resource:db:postgres",
    kind=ResourceKind.DB,
    name="postgres",
    display_name="PostgreSQL",
    users=("api", "web"),
    home_unit=".",
)


def graph_of(language: str, *files: str) -> CallGraph:
    """A graph holding one callable per file, which is what makes a unit's directory code."""
    nodes = {}
    for path in files:
        name = f"{path}|run"
        nodes[name] = Node(name, NodeType.FUNCTION, str(REPO / path), 1, 20)
    return CallGraph(nodes, (), language)


def site(line: int, kind: EdgeKind = EdgeKind.CALLS_HTTP, file: str = "docker-compose.yml") -> Join:
    return Join(
        source="web",
        target="api",
        kind=kind,
        file=file,
        line=line,
        column=1,
        key="http://api:8080",
        family=AnchorFamily.DEPLOYMENT,
    )


def use(source: str, target: str, kind: EdgeKind = EdgeKind.USES, line: int = 5) -> Join:
    return Join(source, target, kind, "docker-compose.yml", line, 1, target.rpartition(":")[2], AnchorFamily.DEPLOYMENT)


def hierarchy_of(*groups: ClusterGroup, **graphs: CallGraph) -> ClusterScopeResult:
    return ClusterScopeResult(scope_id=ROOT_SCOPE_ID, graphs_by_language=dict(graphs), groups=list(groups))


class TestEndpoints(unittest.TestCase):
    def test_a_unit_is_reached_at_its_own_manifest(self) -> None:
        graphs = {"typescript": graph_of("typescript", "api/app.ts")}

        endpoints = endpoint_nodes([API], graphs, REPO)

        self.assertEqual(
            {unit: node.fully_qualified_name for unit, node in endpoints.items()}, {"api": "api/package.json"}
        )
        self.assertEqual(endpoints["api"].type, NodeType.FILE)

    def test_three_units_are_reached_nowhere_and_each_says_so(self) -> None:
        """Nothing to point at, nothing that makes it a box, or the whole tree (§4) — each a row, not a silence."""
        units = [
            Unit(id="a", dir="a", kind=UnitKind.NPM),
            Unit(id="b", dir="b", kind=UnitKind.NPM, manifest="b/package.json"),
            Unit(id=".", dir=".", kind=UnitKind.NPM, manifest="package.json"),
            API,
        ]
        graphs = {"typescript": graph_of("typescript", "a/app.ts", "b_other/app.ts", "api/app.ts")}
        joins = [
            Join("a", "api", EdgeKind.CALLS_HTTP, "compose.yaml", 1, 1, "http://api", AnchorFamily.DEPLOYMENT),
            Join("b", "api", EdgeKind.CALLS_HTTP, "compose.yaml", 2, 1, "http://api", AnchorFamily.DEPLOYMENT),
            Join(".", "api", EdgeKind.CALLS_HTTP, "compose.yaml", 3, 1, "http://api", AnchorFamily.DEPLOYMENT),
        ]

        self.assertEqual(endpoint_nodes(units, graphs, REPO).keys(), {"api"})
        edges, rows = emit(joins, units, [], graphs, REPO)
        self.assertEqual(edges, [])
        self.assertEqual(
            [(row.code, row.message) for row in rows],
            [
                (DiagnosticCode.NO_BOX_FOR_UNIT, ". is the repository itself, which no arrow can land on"),
                (DiagnosticCode.NO_BOX_FOR_UNIT, "a has no manifest to land an arrow on"),
                (DiagnosticCode.NO_BOX_FOR_UNIT, "b holds no analysed code, so no arrow lands on it in P1"),
            ],
        )


class TestEmit(unittest.TestCase):
    def test_two_places_declaring_one_connection_are_one_edge(self) -> None:
        """Sites say where an arrow was declared; they never make it a second arrow."""
        graphs = {"typescript": graph_of("typescript", "api/app.ts", "web/app.ts")}

        edges, rows = emit([site(5), site(9)], [API, WEB], [], graphs, REPO)

        self.assertEqual(rows, [])
        self.assertEqual(len(edges), 1)
        self.assertEqual(
            (edges[0].src, edges[0].dst, edges[0].kind), ("web/package.json", "api/package.json", EdgeKind.CALLS_HTTP)
        )
        self.assertEqual([place["line"] for place in edges[0].sites], [5, 9])

    def test_an_arrow_needs_both_of_its_ends(self) -> None:
        graphs = {"typescript": graph_of("typescript", "web/app.ts")}

        edges, rows = emit([site(5)], [API, WEB], [], graphs, REPO)

        self.assertEqual(edges, [])
        self.assertEqual([row.code for row in rows], [DiagnosticCode.NO_BOX_FOR_UNIT])

    def test_a_literal_in_code_anchors_the_enclosing_symbol(self) -> None:
        """§4: the endpoint is the code symbol that wrote the name, not the unit's manifest."""
        graphs = {"typescript": graph_of("typescript", "api/app.ts", "web/app.ts")}

        edges, _ = emit([site(7, file="web/app.ts")], [API, WEB], [], graphs, REPO)

        self.assertEqual([(edge.src, edge.dst) for edge in edges], [("web/app.ts|run", "api/package.json")])


class TestPlace(unittest.TestCase):
    def _hierarchy(self) -> tuple[ClusterScopeResult, ClusterGroup, ClusterGroup]:
        graph = graph_of("typescript", "api/app.ts", "web/app.ts")
        api = ClusterGroup(group_id="1", cluster_ids=[1], symbol_members_by_language={"typescript": {"api/app.ts|run"}})
        web = ClusterGroup(group_id="2", cluster_ids=[2], symbol_members_by_language={"typescript": {"web/app.ts|run"}})
        return hierarchy_of(api, web, typescript=graph), api, web

    def test_the_wiring_graph_holds_the_endpoints_and_the_language_graphs_are_untouched(self) -> None:
        """Decision 1: a partition never moves, a language graph never carries a wiring edge, and a warm
        start can never re-import last run's arrows through the pickle."""
        hierarchy, api, web = self._hierarchy()
        edges, _ = emit([site(5)], [API, WEB], [], hierarchy.graphs_by_language, REPO)

        placement = place(hierarchy, [API, WEB], edges, [], REPO)

        self.assertEqual(placement.graph.language, WIRING_GRAPH)
        self.assertEqual(sorted(placement.graph.nodes), ["api/package.json", "web/package.json"])
        self.assertEqual(
            [(e.src, e.dst, e.kind) for e in placement.graph.reference_edges],
            [("web/package.json", "api/package.json", EdgeKind.CALLS_HTTP)],
        )
        self.assertEqual(placement.owners, {"api/package.json": "1", "web/package.json": "2"})
        self.assertEqual(hierarchy.graphs_by_language["typescript"].reference_edges, [])
        self.assertEqual(sorted(hierarchy.graphs_by_language["typescript"].nodes), ["api/app.ts|run", "web/app.ts|run"])
        self.assertEqual(api.symbol_members_by_language, {"typescript": {"api/app.ts|run"}})
        self.assertEqual(web.symbol_members_by_language, {"typescript": {"web/app.ts|run"}})

    def test_an_arrow_between_two_languages_is_drawn(self) -> None:
        """The case the layer exists for: a compose file wiring a Python service to a TypeScript one."""
        python = graph_of("python", "api/app.py")
        typescript = graph_of("typescript", "web/app.ts")
        api = ClusterGroup(group_id="1", cluster_ids=[1], symbol_members_by_language={"python": {"api/app.py|run"}})
        web = ClusterGroup(group_id="2", cluster_ids=[2], symbol_members_by_language={"typescript": {"web/app.ts|run"}})
        hierarchy = hierarchy_of(api, web, python=python, typescript=typescript)
        edges, _ = emit([site(5)], [API, WEB], [], hierarchy.graphs_by_language, REPO)

        placement = place(hierarchy, [API, WEB], edges, [], REPO)

        self.assertEqual(
            [(e.src, e.dst) for e in placement.graph.reference_edges], [("web/package.json", "api/package.json")]
        )
        self.assertEqual(placement.owners, {"api/package.json": "1", "web/package.json": "2"})

    def test_a_relation_between_the_boxes_carries_the_kind(self) -> None:
        """The whole path: place, then the relation step reads the wiring graph like any other graph."""
        hierarchy, _, _ = self._hierarchy()
        edges, _ = emit([site(5)], [API, WEB], [], hierarchy.graphs_by_language, REPO)
        placement = place(hierarchy, [API, WEB], edges, [], REPO)
        assembled = ScopeAssembler(REPO).build(hierarchy)
        owners = build_global_node_to_component_map(assembled, {}, placement.owners)
        graphs = {**hierarchy.graphs_by_language, WIRING_GRAPH: placement.graph}

        (relation,) = build_component_relations(owners, graphs)

        self.assertEqual((relation.src_cluster_id, relation.dst_cluster_id), ("2", "1"))
        self.assertEqual([edge.kind for edge in relation.all_edges], [EdgeKind.CALLS_HTTP])
        self.assertEqual(relation.all_edges[0].call_sites[0].file, "docker-compose.yml")

    def test_an_endpoint_lands_on_the_deepest_component_inside_its_unit(self) -> None:
        """At depth 2 a relation between two services' children lands on the children, not the parents."""
        graph = graph_of("typescript", "api/app.ts", "api/jobs/run.ts", "api/jobs/tick.ts", "web/app.ts")
        api_core = ClusterGroup(
            group_id="1.1", cluster_ids=[1], symbol_members_by_language={"typescript": {"api/app.ts|run"}}
        )
        api_jobs = ClusterGroup(
            group_id="1.2",
            cluster_ids=[2],
            symbol_members_by_language={"typescript": {"api/jobs/run.ts|run", "api/jobs/tick.ts|run"}},
        )
        api = ClusterGroup(
            group_id="1",
            cluster_ids=[1, 2],
            symbol_members_by_language={
                "typescript": {"api/app.ts|run", "api/jobs/run.ts|run", "api/jobs/tick.ts|run"}
            },
            children=ClusterScopeResult(
                scope_id="1", graphs_by_language={"typescript": graph}, groups=[api_core, api_jobs]
            ),
        )
        web = ClusterGroup(group_id="2", cluster_ids=[3], symbol_members_by_language={"typescript": {"web/app.ts|run"}})
        hierarchy = hierarchy_of(api, web, typescript=graph)
        edges, _ = emit([site(5)], [API, WEB], [], hierarchy.graphs_by_language, REPO)

        placement = place(hierarchy, [API, WEB], edges, [], REPO)

        self.assertEqual(placement.owners, {"api/package.json": "1.2", "web/package.json": "2"})
        root = ScopeAssembler(REPO).build(hierarchy)
        lifted = build_global_node_to_component_map(root, {}, placement.owners)
        self.assertEqual(lifted["api/package.json"], "1")

    def test_a_unit_whose_only_edges_are_undrawn_gets_no_node(self) -> None:
        """DEPENDS_ON stays in the results for P3 and never enters the graph the relations read."""
        hierarchy, _, _ = self._hierarchy()
        edges, _ = emit([site(5, kind=EdgeKind.DEPENDS_ON)], [API, WEB], [], hierarchy.graphs_by_language, REPO)

        placement = place(hierarchy, [API, WEB], edges, [], REPO)

        self.assertEqual([edge.kind for edge in edges], [EdgeKind.DEPENDS_ON])
        self.assertEqual(placement.graph.nodes, {})
        self.assertEqual(placement.owners, {})

    def test_a_code_symbol_endpoint_joins_the_graph_and_needs_no_owner(self) -> None:
        hierarchy, _, _ = self._hierarchy()
        edges, _ = emit([site(7, file="web/app.ts")], [API, WEB], [], hierarchy.graphs_by_language, REPO)

        placement = place(hierarchy, [API, WEB], edges, [], REPO)

        self.assertEqual(sorted(placement.graph.nodes), ["api/package.json", "web/app.ts|run"])
        self.assertEqual(placement.owners, {"api/package.json": "1"})

    def test_placing_changes_nothing_a_later_run_would_compare(self) -> None:
        """An incremental run compares persisted members against live groups; an endpoint is in neither."""
        hierarchy, _, _ = self._hierarchy()
        edges, _ = emit([site(5)], [API, WEB], [], hierarchy.graphs_by_language, REPO)
        place(hierarchy, [API, WEB], edges, [], REPO)

        persisted = ScopeAssembler(REPO).build(hierarchy)

        self.assertFalse(hierarchy_differs(hierarchy, {ROOT_SCOPE_ID: persisted}))
        self.assertEqual(sorted(persisted.files), ["api/app.ts", "web/app.ts"])

    def test_with_no_edges_nothing_is_placed(self) -> None:
        """The flag-off guarantee: a run that draws no wiring leaves every box as it was."""
        hierarchy, api, web = self._hierarchy()

        placement = place(hierarchy, [API, WEB], [], [], REPO)

        self.assertEqual((placement.graph.nodes, placement.owners), ({}, {}))
        self.assertEqual(api.symbol_members_by_language, {"typescript": {"api/app.ts|run"}})
        self.assertEqual(web.symbol_members_by_language, {"typescript": {"web/app.ts|run"}})


class TestResourceNodes(unittest.TestCase):
    def _hierarchy(self) -> ClusterScopeResult:
        graph = graph_of("typescript", "api/app.ts", "web/app.ts")
        api = ClusterGroup(group_id="1", cluster_ids=[1], symbol_members_by_language={"typescript": {"api/app.ts|run"}})
        web = ClusterGroup(group_id="2", cluster_ids=[2], symbol_members_by_language={"typescript": {"web/app.ts|run"}})
        return hierarchy_of(api, web, typescript=graph)

    def test_a_use_of_a_resource_is_an_edge_into_a_node_that_owns_itself(self) -> None:
        """A resource node is its own end of a relation: it belongs to no box, has no file, and is an
        OBJECT, a type no member index, file coverage or symbol count reads as code (§7)."""
        hierarchy = self._hierarchy()
        edges, rows = emit(
            [use("web", "resource:cache:redis")], [API, WEB], [REDIS], hierarchy.graphs_by_language, REPO
        )

        placement = place(hierarchy, [API, WEB], edges, [REDIS], REPO)

        self.assertEqual(rows, [])
        self.assertEqual(
            [(e.src, e.dst, e.kind) for e in edges], [("web/package.json", "resource:cache:redis", EdgeKind.USES)]
        )
        node = placement.graph.nodes["resource:cache:redis"]
        self.assertEqual((node.type, node.file_path), (NodeType.OBJECT, ""))
        self.assertEqual(placement.owners, {"web/package.json": "2", "resource:cache:redis": "resource:cache:redis"})
        self.assertEqual(placement.labels, {"resource:cache:redis": "Redis"})

    def test_two_resources_without_a_file_are_two_nodes(self) -> None:
        """Nodes without a file share no location, so the graph never folds one into another."""
        hierarchy = self._hierarchy()
        joins = [use("web", "resource:cache:redis"), use("web", "resource:db:postgres", line=6)]
        edges, _ = emit(joins, [API, WEB], [REDIS, POSTGRES], hierarchy.graphs_by_language, REPO)

        placement = place(hierarchy, [API, WEB], edges, [REDIS, POSTGRES], REPO)

        self.assertEqual(
            sorted(placement.graph.nodes), ["resource:cache:redis", "resource:db:postgres", "web/package.json"]
        )
        self.assertEqual(len(placement.graph.reference_edges), 2)

    def test_a_resource_s_home_is_its_sole_user_s_box_or_where_its_users_meet(self) -> None:
        """Redis has one user, so it lives in that user's box; PostgreSQL has two, in two top-level
        boxes, so it is drawn at the top (§7)."""
        hierarchy = self._hierarchy()

        placement = place(hierarchy, [API, WEB], [], [REDIS, POSTGRES], REPO)

        self.assertEqual(placement.homes, {"resource:cache:redis": "2", "resource:db:postgres": ""})
        self.assertEqual(placement.graph.nodes, {})

    def test_the_box_users_meet_in_is_their_deepest_common_ancestor(self) -> None:
        self.assertEqual(common_ancestor(["1.1", "1.2"]), "1")
        self.assertEqual(common_ancestor(["1.1.3", "1.1.4"]), "1.1")
        self.assertEqual(common_ancestor(["1", "2"]), "")
        self.assertEqual(common_ancestor(["1.1"]), "1.1")
        # A user no box holds (a unit with no analysed code) makes the meeting point the top.
        self.assertEqual(common_ancestor(["1.1", ""]), "")
        self.assertEqual(common_ancestor([]), "")

    def test_a_relation_into_a_resource_carries_its_key_and_its_name(self) -> None:
        hierarchy = self._hierarchy()
        edges, _ = emit([use("web", "resource:cache:redis")], [API, WEB], [REDIS], hierarchy.graphs_by_language, REPO)
        placement = place(hierarchy, [API, WEB], edges, [REDIS], REPO)
        root = ScopeAssembler(REPO).build(hierarchy)

        lifted = build_global_node_to_component_map(root, {}, placement.owners)
        relations = build_global_relations(
            root, {}, {WIRING_GRAPH: placement.graph}, placement.owners, placement.labels
        )

        self.assertEqual(lifted["resource:cache:redis"], "resource:cache:redis")
        (relation,) = [r for r in relations if r.dst_id == "resource:cache:redis"]
        self.assertEqual((relation.src_id, relation.dst_name, relation.relation), ("2", "Redis", "uses"))
        self.assertEqual([edge.kind for edge in relation.all_edges], [EdgeKind.USES])

    def test_the_document_writes_a_resource_end_as_its_key_and_reads_it_back(self) -> None:
        """`src_id`/`dst_id` carry the key; an edge end is `|<key>`, which is what the loader parses
        back into a reference with no file (§8)."""
        hierarchy = self._hierarchy()
        edges, _ = emit([use("web", "resource:cache:redis")], [API, WEB], [REDIS], hierarchy.graphs_by_language, REPO)
        placement = place(hierarchy, [API, WEB], edges, [REDIS], REPO)
        root = ScopeAssembler(REPO).build(hierarchy)
        root.components_relations = build_global_relations(
            root, {}, {WIRING_GRAPH: placement.graph}, placement.owners, placement.labels
        )

        document = json.loads(build_unified_analysis_json(root, [], "repo", REPO, "hash", 1, resources=[REDIS]))

        (relation,) = [r for r in document["components_relations"] if r["dst_id"] == "resource:cache:redis"]
        (edge,) = relation["all_edges"]
        self.assertEqual((edge["target"], edge["kind"]), ("|resource:cache:redis", "uses"))
        self.assertNotIn("infrastructure", relation)
        read = RelationEdge.from_dict(edge, {})
        self.assertEqual((read.target.qualified_name, read.target.reference_file), ("resource:cache:redis", None))

    def test_a_relation_of_nothing_but_infrastructure_says_so(self) -> None:
        """A star of registering, fetching and reporting arrows says the same thing about every
        service, so the document marks it for a reader to fold (§5)."""
        hierarchy = self._hierarchy()
        joins = [use("web", "resource:cache:redis", EdgeKind.REPORTS_TO)]
        edges, _ = emit(joins, [API, WEB], [REDIS], hierarchy.graphs_by_language, REPO)
        placement = place(hierarchy, [API, WEB], edges, [REDIS], REPO)
        root = ScopeAssembler(REPO).build(hierarchy)
        root.components_relations = build_global_relations(
            root, {}, {WIRING_GRAPH: placement.graph}, placement.owners, placement.labels
        )

        document = json.loads(build_unified_analysis_json(root, [], "repo", REPO, "hash", 1, resources=[REDIS]))

        (relation,) = [r for r in document["components_relations"] if r["dst_id"] == "resource:cache:redis"]
        self.assertEqual((relation["relation"], relation["infrastructure"]), ("reports to", True))


class TestArtifactFiles(unittest.TestCase):
    def test_an_endpoint_is_indexed_from_the_wiring_graph_and_owned_by_no_component(self) -> None:
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        (root / "api").mkdir()
        (root / "api" / "package.json").write_text('{"name": "api"}\n', encoding="utf-8")
        analysis = AnalysisInsights(description="", components=[], components_relations=[])
        node = Node("api/package.json", NodeType.FILE, str(root / "api" / "package.json"), 1, 1)

        index_artifact_files(analysis, [node], root)

        (entry,) = analysis.files.values()
        self.assertEqual(list(analysis.files), ["api/package.json"])
        self.assertEqual([(m.qualified_name, m.node_type) for m in entry.methods], [("api/package.json", "FILE")])
        self.assertTrue(entry.content_hash and entry.methods[0].content_hash)


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
