from pathlib import Path
from unittest.mock import MagicMock

from static_analyzer.analysis_cache import StaticAnalysisCache
from static_analyzer.analysis_result import StaticAnalysisResults
from static_analyzer.cfg import CallGraph, Edge
from static_analyzer.cluster_relations import build_component_relations
from static_analyzer.clustering.models import ClusterGroup
from static_analyzer.clustering.service import ClusteringService, unit_links
from static_analyzer.config import Language, NodeType
from static_analyzer.engine.adapters.kotlin_adapter import KotlinAdapter
from static_analyzer.engine.models import CallSite, ExternalCallSite
from static_analyzer.engine.source_inspector import SourceInspector
from static_analyzer.external_calls import (
    callers_of_changed_targets,
    carry_cross_language_edges,
    link_across_languages,
)
from static_analyzer.graph_definitions import OVERRIDE
from static_analyzer.node import Node
from static_analyzer.reference_resolver import StaticReferenceResolver

REPO = Path("/repo")
SERVER = str(REPO / "src/main/kotlin/web/Server.kt")
HELPER = str(REPO / "src/main/kotlin/web/Helper.kt")
JAVALIN = str(REPO / "src/main/java/io/javalin/Javalin.java")
CALLER = "src.main.kotlin.web.Server.start"
CREATE = "io.javalin.Javalin.create()"


def _node(qname: str, kind: NodeType, file: str, line: int, col: int = 4, end: int = 0) -> Node:
    return Node(qname, kind, file, line_start=line, line_end=end or line + 3, col_start=col)


def _results() -> StaticAnalysisResults:
    """A Kotlin graph that calls into a Java one, as the two engines leave them."""
    results = StaticAnalysisResults()
    kotlin = CallGraph(language="kotlin")
    kotlin.add_node(_node(CALLER, NodeType.METHOD, SERVER, 10))
    kotlin.add_node(_node("src.main.kotlin.web.Helper.help", NodeType.FUNCTION, HELPER, 4))
    java = CallGraph(language="java")
    java.add_node(_node("io.javalin.Javalin", NodeType.CLASS, JAVALIN, 3, 0, 60))
    java.add_node(_node(CREATE, NodeType.METHOD, JAVALIN, 20))
    results.add_cfg(Language.KOTLIN, kotlin)
    results.add_cfg(Language.JAVA, java)
    return results


def _site(file: str, line: int, character: int, kind: str = "call", at_line: int = 12) -> ExternalCallSite:
    return ExternalCallSite(CALLER, file, line, character, CallSite(SERVER, at_line, 9), kind)


def _link(results: StaticAnalysisResults, sites: list[ExternalCallSite]) -> list[Edge]:
    return link_across_languages(results, Language.KOTLIN, sites, KotlinAdapter(), SourceInspector())


def _pairs(edges: list[Edge]) -> set[tuple[str, str]]:
    return {(edge.get_source(), edge.get_destination()) for edge in edges}


class TestLinkAcrossLanguages:
    def test_a_kotlin_call_into_a_java_method_is_an_edge_with_its_call_site(self):
        results = _results()

        edges = _link(results, [_site(JAVALIN, 19, 4)])

        assert _pairs(edges) == {(CALLER, CREATE), (CALLER, "io.javalin.Javalin")}
        create = next(edge for edge in edges if edge.get_destination() == CREATE)
        assert [(s["file"], s["line"], s["column"]) for s in create.call_sites] == [(SERVER, 12, 9)]
        assert results.get_cfg(Language.KOTLIN).edges == []
        assert results.get_cfg(Language.JAVA).edges == []

    def test_the_edge_ends_on_the_java_graphs_own_node(self):
        results = _results()

        edges = _link(results, [_site(JAVALIN, 19, 4)])

        create = next(edge for edge in edges if edge.get_destination() == CREATE)
        assert create.dst_node is results.get_cfg(Language.JAVA).nodes[CREATE]
        assert create.src_node is results.get_cfg(Language.KOTLIN).nodes[CALLER]

    def test_a_site_in_a_file_of_the_callers_own_language_is_left_alone(self):
        assert _link(_results(), [_site(HELPER, 3, 4)]) == []

    def test_a_site_in_a_language_no_engine_analysed_adds_nothing(self):
        results = StaticAnalysisResults()
        kotlin = CallGraph(language="kotlin")
        kotlin.add_node(_node(CALLER, NodeType.METHOD, SERVER, 10))
        results.add_cfg(Language.KOTLIN, kotlin)

        assert _link(results, [_site(JAVALIN, 19, 4)]) == []

    def test_a_site_outside_any_analysed_suffix_adds_nothing(self):
        assert _link(_results(), [_site("/jdk/lib/src.zip!/java/util/List.class", 19, 4)]) == []

    def test_an_override_site_is_left_out(self):
        assert _link(_results(), [_site(JAVALIN, 19, 4, OVERRIDE)]) == []

    def test_a_caller_no_graph_holds_adds_nothing(self):
        site = ExternalCallSite("gone", JAVALIN, 19, 4, CallSite(SERVER, 12, 9))

        assert _link(_results(), [site]) == []

    def test_two_sites_of_one_call_pair_share_one_edge(self):
        edges = _link(_results(), [_site(JAVALIN, 19, 4, at_line=12), _site(JAVALIN, 19, 4, at_line=14)])

        create = next(edge for edge in edges if edge.get_destination() == CREATE)
        assert [s["line"] for s in create.call_sites] == [12, 14]


class TestResultsHoldCrossLanguageEdges:
    def test_adding_an_edge_twice_merges_its_call_sites(self):
        results = _results()
        caller = results.get_cfg(Language.KOTLIN).nodes[CALLER]
        target = results.get_cfg(Language.JAVA).nodes[CREATE]

        results.add_cross_language_edges([Edge(caller, target, [{"file": SERVER, "line": 12, "column": 9}])])
        results.add_cross_language_edges([Edge(caller, target, [{"file": SERVER, "line": 14, "column": 9}])])

        assert len(results.cross_language_edges) == 1
        assert [s["line"] for s in results.cross_language_edges[0].call_sites] == [12, 14]

    def test_call_edges_include_both_languages_and_the_edges_between_them(self):
        results = _results()
        kotlin = results.get_cfg(Language.KOTLIN)
        kotlin.add_edge(CALLER, "src.main.kotlin.web.Helper.help")
        results.add_cross_language_edges(_link(results, [_site(JAVALIN, 19, 4)]))

        assert {(edge.get_source(), edge.get_destination()) for edge in results.call_edges()} == {
            (CALLER, "src.main.kotlin.web.Helper.help"),
            (CALLER, CREATE),
            (CALLER, "io.javalin.Javalin"),
        }

    def test_the_cache_round_trip_keeps_the_edge_and_its_call_site(self, tmp_path: Path):
        repo = tmp_path / "repo"
        server, javalin = repo / "Server.kt", repo / "Javalin.java"
        results = StaticAnalysisResults()
        kotlin = CallGraph(language="kotlin")
        kotlin.add_node(_node("Server.start", NodeType.METHOD, str(server), 10))
        java = CallGraph(language="java")
        java.add_node(_node("Javalin.create()", NodeType.METHOD, str(javalin), 20))
        results.add_cfg(Language.KOTLIN, kotlin)
        results.add_cfg(Language.JAVA, java)
        results.add_cross_language_edges(
            [Edge(kotlin.nodes["Server.start"], java.nodes["Javalin.create()"], [{"file": str(server), "line": 12}])]
        )
        cache = StaticAnalysisCache(tmp_path / "artifacts", repo)

        cache.save(results, "sha")
        loaded = cache.load_with_sha()

        assert loaded is not None
        edge = loaded[0].cross_language_edges[0]
        assert (edge.src_node.file_path, edge.dst_node.file_path) == (str(server), str(javalin))
        assert edge.call_sites[0]["file"] == str(server)
        assert edge.dst_node is loaded[0].get_cfg(Language.JAVA).nodes["Javalin.create()"]


class TestCarryAcrossAWarmStart:
    def _cached(self) -> StaticAnalysisResults:
        cached = _results()
        cached.add_cross_language_edges(_link(cached, [_site(JAVALIN, 19, 4)]))
        return cached

    def test_an_edge_from_an_unchanged_file_is_kept_on_the_rebuilt_nodes(self):
        rebuilt = _results()

        kept = carry_cross_language_edges(self._cached(), rebuilt, set(), set())

        assert _pairs(kept) == {(CALLER, CREATE), (CALLER, "io.javalin.Javalin")}
        create = next(edge for edge in kept if edge.get_destination() == CREATE)
        assert create.dst_node is rebuilt.get_cfg(Language.JAVA).nodes[CREATE]
        assert [s["line"] for s in create.call_sites] == [12]

    def test_an_edge_from_a_reanalysed_file_is_dropped(self):
        assert carry_cross_language_edges(self._cached(), _results(), {SERVER}, set()) == []

    def test_an_edge_from_a_language_analysed_in_full_again_is_dropped(self):
        assert carry_cross_language_edges(self._cached(), _results(), set(), {Language.KOTLIN}) == []

    def test_an_edge_whose_target_is_gone_is_dropped(self):
        rebuilt = StaticAnalysisResults()
        kotlin = CallGraph(language="kotlin")
        kotlin.add_node(_node(CALLER, NodeType.METHOD, SERVER, 10))
        java = CallGraph(language="java")
        java.add_node(_node("io.javalin.Javalin", NodeType.CLASS, JAVALIN, 3, 0, 60))
        rebuilt.add_cfg(Language.KOTLIN, kotlin)
        rebuilt.add_cfg(Language.JAVA, java)

        kept = carry_cross_language_edges(self._cached(), rebuilt, set(), set())

        assert _pairs(kept) == {(CALLER, "io.javalin.Javalin")}

    def test_a_caller_whose_java_target_changed_is_analysed_again(self):
        """Why: a new Java overload can bind the unchanged Kotlin call elsewhere."""
        assert callers_of_changed_targets(self._cached(), {Path(JAVALIN)}, Language.KOTLIN) == {Path(SERVER)}

    def test_a_change_no_cross_language_call_ends_in_adds_no_caller(self):
        assert callers_of_changed_targets(self._cached(), {Path(HELPER)}, Language.KOTLIN) == set()

    def test_the_java_pass_does_not_take_the_kotlin_callers(self):
        assert callers_of_changed_targets(self._cached(), {Path(JAVALIN)}, Language.JAVA) == set()


class TestConsumersSeeCrossLanguageEdges:
    def test_a_cross_language_edge_relates_the_two_components(self):
        results = _results()
        edges = _link(results, [_site(JAVALIN, 19, 4)])
        owners = {CALLER: "1", CREATE: "2", "io.javalin.Javalin": "2"}

        relations = build_component_relations(owners, results.available_cfgs(), edges)

        assert [(r.src_cluster_id, r.dst_cluster_id) for r in relations] == [("1", "2")]

    def test_a_cross_language_edge_links_the_two_files(self):
        results = _results()
        edges = _link(results, [_site(JAVALIN, 19, 4)])

        assert unit_links(results.available_cfgs(), edges) == {(SERVER, JAVALIN): 2}

    def test_a_cross_language_edge_connects_the_two_groups_with_each_end_in_its_own_language(self):
        results = _results()
        edges = _link(results, [_site(JAVALIN, 19, 4)])
        groups = [
            ClusterGroup("g1", [], symbol_members_by_language={"kotlin": {CALLER}}),
            ClusterGroup("g2", [], symbol_members_by_language={"java": {CREATE, "io.javalin.Javalin"}}),
        ]

        (connection,) = ClusteringService._build_connections(results.available_cfgs(), groups, edges)

        assert (connection.source_group_id, connection.target_group_id) == ("g1", "g2")
        assert {(e.language, e.target_graph_language) for e in connection.edges} == {("kotlin", "java")}

    def test_a_relation_finds_the_cross_language_edge_for_its_call_sites(self):
        results = _results()
        results.add_cross_language_edges(_link(results, [_site(JAVALIN, 19, 4)]))
        relation = MagicMock()
        relation.source.qualified_name, relation.target.qualified_name = CALLER, CREATE

        edge = StaticReferenceResolver(REPO, results).find_static_edge(relation)

        assert edge is not None and edge.call_sites


class TestAnswersAtTheStartOfAJavaDeclaration:
    """A server can answer a Java target with the whole element, doc comment and annotations included."""

    SOURCE = (
        "package io.javalin;\n"
        "\n"
        "public class Javalin {\n"
        "    /**\n"
        "     * Creates an instance.\n"
        "     */\n"
        "    @NotNull\n"
        "    public static Javalin create() {\n"
        "        return new Javalin();\n"
        "    }\n"
        "}\n"
    )

    def _results(self, java_file: Path) -> StaticAnalysisResults:
        results = StaticAnalysisResults()
        kotlin = CallGraph(language="kotlin")
        kotlin.add_node(_node(CALLER, NodeType.METHOD, SERVER, 10))
        java = CallGraph(language="java")
        java.add_node(_node("io.javalin.Javalin", NodeType.CLASS, str(java_file), 3, 13, 11))
        java.add_node(_node(CREATE, NodeType.METHOD, str(java_file), 8, 26, 10))
        results.add_cfg(Language.KOTLIN, kotlin)
        results.add_cfg(Language.JAVA, java)
        return results

    def test_an_answer_at_the_doc_comment_names_the_method(self, tmp_path: Path):
        java_file = tmp_path / "Javalin.java"
        java_file.write_text(self.SOURCE)

        edges = _link(self._results(java_file), [_site(str(java_file), 3, 4)])

        assert (CALLER, CREATE) in _pairs(edges)

    def test_an_answer_at_the_annotation_names_the_method(self, tmp_path: Path):
        java_file = tmp_path / "Javalin.java"
        java_file.write_text(self.SOURCE)

        edges = _link(self._results(java_file), [_site(str(java_file), 6, 4)])

        assert (CALLER, CREATE) in _pairs(edges)

    def test_an_answer_inside_a_body_names_nothing(self, tmp_path: Path):
        java_file = tmp_path / "Javalin.java"
        java_file.write_text(self.SOURCE)

        assert _link(self._results(java_file), [_site(str(java_file), 8, 8)]) == []
