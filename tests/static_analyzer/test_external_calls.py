from static_analyzer.cfg import CallGraph, EdgeKind, ReferenceEdge
from static_analyzer.config import NodeType
from static_analyzer.engine.adapters.csharp_adapter import CSharpAdapter
from static_analyzer.engine.models import CallSite, ExternalCallSite
from static_analyzer.engine.source_inspector import SourceInspector
from static_analyzer.external_calls import link_external_call_sites
from static_analyzer.graph_definitions import COLLECTION_INITIALIZER, ITERATED, METHOD_GROUP
from static_analyzer.node import Node

CALLER = "Modules.Host.Configure(App app)"
BUILDER = "/repo/framework/Builder.cs"


def _node(qname: str, kind: NodeType, file: str, line: int, col: int = 4, end: int = 0) -> Node:
    return Node(qname, kind, file, line_start=line, line_end=end or line + 3, col_start=col)


def _graph() -> CallGraph:
    """Two engines' worth of nodes after the merge: a module and the framework it calls."""
    graph = CallGraph(language="csharp")
    graph.add_node(_node(CALLER, NodeType.METHOD, "/repo/modules/Host.cs", 10))
    graph.add_node(_node("Modules.Host", NodeType.CLASS, "/repo/modules/Host.cs", 3, 0, 40))
    graph.add_node(_node("Framework.Builder", NodeType.CLASS, BUILDER, 3, 0, 60))
    graph.add_node(_node("Framework.Builder.UseAuditing(App app)", NodeType.METHOD, BUILDER, 20))
    graph.add_node(_node("Framework.Builder.UseAuditing(App app, int depth)", NodeType.METHOD, BUILDER, 30))
    graph.add_node(_node("Framework.Builder.Status", NodeType.ENUM, BUILDER, 40, 4, 40))
    graph.add_node(_node("Framework.Bag", NodeType.CLASS, "/repo/framework/Bag.cs", 3, 0, 30))
    graph.add_node(_node("Framework.Bag.Add(int item)", NodeType.METHOD, "/repo/framework/Bag.cs", 8))
    graph.add_node(_node("Framework.Bag.GetEnumerator()", NodeType.METHOD, "/repo/framework/Bag.cs", 14))
    graph.add_node(_node("Framework.Loud", NodeType.CLASS, "/repo/framework/Loud.cs", 3, 0, 30))
    graph.add_node(_node("Framework.Loud.UseAuditing(App app)", NodeType.METHOD, "/repo/framework/Loud.cs", 8))
    graph.add_reference_edge(ReferenceEdge("Framework.Loud", "Framework.Builder", EdgeKind.INHERITS))
    return graph


def _site(file: str, line: int, character: int, kind: str = "call", at_line: int = 12) -> ExternalCallSite:
    return ExternalCallSite(CALLER, file, line, character, CallSite("/repo/modules/Host.cs", at_line, 9), kind)


def _link(graph: CallGraph, sites: list[ExternalCallSite], packages: dict | None = None) -> int:
    return link_external_call_sites(
        graph, sites, CSharpAdapter(), packages if packages is not None else {}, SourceInspector()
    )


def _destinations(graph: CallGraph) -> set[str]:
    return {edge.get_destination() for edge in graph.edges}


def test_a_definition_in_another_engines_file_becomes_an_edge_with_its_call_site():
    graph = _graph()

    added = _link(graph, [_site(BUILDER, 19, 4)])

    # The method, its class, and the override the INHERITS edge names: what the engine itself adds.
    assert added == 3
    edges = {(e.get_source(), e.get_destination()): e for e in graph.edges}
    edge = edges[(CALLER, "Framework.Builder.UseAuditing(App app)")]
    assert [(s["file"], s["line"], s["column"]) for s in edge.call_sites] == [("/repo/modules/Host.cs", 12, 9)]
    assert (CALLER, "Framework.Builder") in edges
    assert (CALLER, "Framework.Loud.UseAuditing(App app)") in edges


def test_exact_position_beats_a_neighbouring_line():
    """Two overloads a few lines apart: the selection start decides."""
    graph = _graph()

    _link(graph, [_site(BUILDER, 29, 4)])

    assert "Framework.Builder.UseAuditing(App app, int depth)" in _destinations(graph)
    assert "Framework.Builder.UseAuditing(App app)" not in _destinations(graph)


def test_a_position_inside_a_declaration_resolves_to_it():
    graph = _graph()

    _link(graph, [_site(BUILDER, 21, 8)])

    assert "Framework.Builder.UseAuditing(App app)" in _destinations(graph)


def test_a_method_group_probe_that_is_a_value_adds_nothing():
    """``Use(Status.Open)`` probes ``Open`` as a possible method group. The member is no node,
    and the one-line enum around it must not stand in for it."""
    graph = _graph()

    assert _link(graph, [_site(BUILDER, 39, 20, METHOD_GROUP)]) == 0


def test_a_method_group_probe_that_is_a_method_is_an_edge():
    graph = _graph()

    _link(graph, [_site(BUILDER, 19, 4, METHOD_GROUP)])

    assert {"Framework.Builder.UseAuditing(App app)", "Framework.Builder"} <= _destinations(graph)


def test_a_collection_initializer_reaches_add():
    graph = _graph()

    _link(graph, [_site("/repo/framework/Bag.cs", 2, 0, COLLECTION_INITIALIZER)])

    assert {"Framework.Bag", "Framework.Bag.Add(int item)"} <= _destinations(graph)


def test_a_foreach_reaches_the_enumerator():
    graph = _graph()

    _link(graph, [_site("/repo/framework/Bag.cs", 2, 0, ITERATED)])

    assert {"Framework.Bag", "Framework.Bag.GetEnumerator()"} <= _destinations(graph)


def test_a_position_no_engine_named_adds_nothing():
    graph = _graph()

    assert _link(graph, [_site("/repo/vendor/Lib.cs", 5, 0)]) == 0
    assert graph.edges == []


def test_a_call_into_the_callers_own_class_is_not_an_edge():
    graph = _graph()

    assert _link(graph, [_site("/repo/modules/Host.cs", 2, 0)]) == 0


def test_a_repeated_site_adds_no_second_edge():
    graph = _graph()
    site = _site(BUILDER, 19, 4)

    _link(graph, [site, site])

    edge = next(e for e in graph.edges if e.get_destination().endswith("Builder.UseAuditing(App app)"))
    assert len(edge.call_sites) == 1


def test_a_linked_edge_across_packages_is_a_package_import():
    """Each engine derived its package relations before the merge; the health checks read them."""
    graph = _graph()
    packages: dict[str, dict] = {
        "Modules": {"imports": [], "imported_by": []},
        "Framework": {"imports": [], "imported_by": []},
    }

    _link(graph, [_site(BUILDER, 19, 4)], packages)

    assert packages["Modules"]["imports"] == ["Framework"]
    assert packages["Framework"]["imported_by"] == ["Modules"]


def test_call_sites_are_one_based_like_the_engines():
    site = ExternalCallSite(CALLER, BUILDER, 19, 4, CallSite.from_lsp_position("/repo/modules/Host.cs", 11, 8))
    graph = _graph()

    _link(graph, [site])

    edge = next(e for e in graph.edges if e.get_destination().endswith("Builder.UseAuditing(App app)"))
    assert (edge.call_sites[0]["line"], edge.call_sites[0]["column"]) == (12, 9)
