from collections.abc import Callable
from pathlib import Path
from unittest.mock import MagicMock

from static_analyzer.cfg import CallGraph, EdgeKind, ReferenceEdge
from static_analyzer.config import NodeType
from static_analyzer.engine.adapters.csharp_adapter import CSharpAdapter
from static_analyzer.engine.models import CallSite, ExternalCallSite
from static_analyzer.engine.source_inspector import SourceInspector
from static_analyzer.external_calls import (
    LinkedCalls,
    link_external_call_sites,
    record_package_imports,
)
from static_analyzer.graph_definitions import (
    COLLECTION_INITIALIZER,
    IMPLEMENTATION,
    ITERATED,
    METHOD_GROUP,
    OVERRIDE,
    RECEIVER,
    GraphIndex,
    potential_calls,
)
from static_analyzer.node import Node

CALLER = "Modules.Host.Configure(App app)"
HOST = "/repo/modules/Host.cs"
BUILDER = "/repo/framework/Builder.cs"
BAG = "/repo/framework/Bag.cs"
LOUD = "/repo/framework/Loud.cs"


def _node(qname: str, kind: NodeType, file: str, line: int, col: int = 4, end: int = 0) -> Node:
    return Node(qname, kind, file, line_start=line, line_end=end or line + 3, col_start=col)


def _graph() -> CallGraph:
    """Two engines' worth of nodes after the merge: a module and the framework it calls."""
    graph = CallGraph(language="csharp")
    graph.add_node(_node(CALLER, NodeType.METHOD, HOST, 10))
    graph.add_node(_node("Modules.Host", NodeType.CLASS, HOST, 3, 0, 40))
    graph.add_node(_node("Framework.Builder", NodeType.CLASS, BUILDER, 3, 0, 60))
    graph.add_node(_node("Framework.Builder.UseAuditing(App app)", NodeType.METHOD, BUILDER, 20))
    graph.add_node(_node("Framework.Builder.UseAuditing(App app, int depth)", NodeType.METHOD, BUILDER, 30))
    graph.add_node(_node("Framework.Builder.Status", NodeType.ENUM, BUILDER, 40, 4, 40))
    graph.add_node(_node("Framework.Bag", NodeType.CLASS, BAG, 3, 0, 30))
    graph.add_node(_node("Framework.Bag.Add(int item)", NodeType.METHOD, BAG, 8))
    graph.add_node(_node("Framework.Bag.GetEnumerator()", NodeType.METHOD, BAG, 14))
    graph.add_node(_node("Framework.Loud", NodeType.CLASS, LOUD, 3, 0, 30))
    graph.add_node(_node("Framework.Loud.UseAuditing(App app)", NodeType.METHOD, LOUD, 8))
    graph.add_reference_edge(ReferenceEdge("Framework.Loud", "Framework.Builder", EdgeKind.INHERITS))
    return graph


def _site(
    file: str, line: int, character: int, kind: str = "call", at_line: int = 12, member: str = ""
) -> ExternalCallSite:
    return ExternalCallSite(CALLER, file, line, character, CallSite(HOST, at_line, 9), kind, member)


def _link(
    graph: CallGraph,
    sites: list[ExternalCallSite],
    analysed: frozenset[str] = frozenset({HOST}),
) -> LinkedCalls:
    return link_external_call_sites(GraphIndex(graph), sites, CSharpAdapter(), SourceInspector(), analysed)


def _destinations(graph: CallGraph) -> set[str]:
    return {edge.get_destination() for edge in graph.edges}


def _edges(graph: CallGraph) -> list[tuple[str, str]]:
    return [(edge.get_source(), edge.get_destination()) for edge in graph.edges]


def test_a_definition_in_another_engines_file_becomes_an_edge_with_its_call_site():
    graph = _graph()

    linked = _link(graph, [_site(BUILDER, 19, 4)])

    # The method, its class, and the override the INHERITS edge names: what the engine itself adds.
    assert len(linked.edges) == 3
    edges = {(e.get_source(), e.get_destination()): e for e in graph.edges}
    edge = edges[(CALLER, "Framework.Builder.UseAuditing(App app)")]
    assert [(s["file"], s["line"], s["column"]) for s in edge.call_sites] == [(HOST, 12, 9)]
    assert (CALLER, "Framework.Builder") in edges
    assert (CALLER, "Framework.Loud.UseAuditing(App app)") in edges


def test_exact_position_beats_a_neighbouring_line():
    """Two overloads a few lines apart: the selection start decides."""
    graph = _graph()

    _link(graph, [_site(BUILDER, 29, 4)])

    assert "Framework.Builder.UseAuditing(App app, int depth)" in _destinations(graph)
    assert "Framework.Builder.UseAuditing(App app)" not in _destinations(graph)


def test_a_position_inside_a_declaration_body_names_nothing():
    """The neighbouring-line match bound a call written in a body to the method around it."""
    graph = _graph()

    assert _link(graph, [_site(BUILDER, 21, 8)]).edges == []


def test_a_position_one_line_off_a_declaration_names_nothing():
    graph = _graph()

    assert _link(graph, [_site(BUILDER, 18, 4)]).edges == []


def test_a_method_group_probe_that_is_a_value_adds_nothing():
    """``Use(Status.Open)`` probes ``Open`` as a possible method group. The member is no node,
    and the one-line enum around it must not stand in for it."""
    graph = _graph()

    assert _link(graph, [_site(BUILDER, 39, 20, METHOD_GROUP)]).edges == []


def test_a_method_group_probe_that_is_a_method_is_an_edge():
    graph = _graph()

    _link(graph, [_site(BUILDER, 19, 4, METHOD_GROUP)])

    assert {"Framework.Builder.UseAuditing(App app)", "Framework.Builder"} <= _destinations(graph)


def test_a_collection_initializer_reaches_add():
    graph = _graph()

    _link(graph, [_site(BAG, 2, 0, COLLECTION_INITIALIZER)])

    assert {"Framework.Bag", "Framework.Bag.Add(int item)"} <= _destinations(graph)


def test_a_foreach_reaches_the_enumerator():
    graph = _graph()

    _link(graph, [_site(BAG, 2, 0, ITERATED)])

    assert {"Framework.Bag", "Framework.Bag.GetEnumerator()"} <= _destinations(graph)


def test_a_position_no_engine_named_is_handed_back():
    """Another solution's engine may still name it once every graph is merged."""
    graph = _graph()
    site = _site("/repo/vendor/Lib.cs", 5, 0)

    linked = _link(graph, [site])

    assert graph.edges == []
    assert linked.unresolved == [site]


def test_a_site_whose_caller_is_no_node_is_dropped():
    graph = _graph()
    site = ExternalCallSite("Gone.Caller()", BUILDER, 19, 4, CallSite(HOST, 12, 9))

    linked = _link(graph, [site])

    assert graph.edges == []
    assert linked.unresolved == []


def test_a_call_into_the_callers_own_class_is_not_an_edge():
    graph = _graph()

    assert _link(graph, [_site(HOST, 2, 0)]).edges == []


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

    record_package_imports(packages, CSharpAdapter(), _link(graph, [_site(BUILDER, 19, 4)]).edges)

    assert packages["Modules"]["imports"] == ["Framework"]
    assert packages["Framework"]["imported_by"] == ["Modules"]


def test_call_sites_are_one_based_like_the_engines():
    site = ExternalCallSite(CALLER, BUILDER, 19, 4, CallSite.from_lsp_position(HOST, 11, 8))
    graph = _graph()

    _link(graph, [site])

    edge = next(e for e in graph.edges if e.get_destination().endswith("Builder.UseAuditing(App app)"))
    assert (edge.call_sites[0]["line"], edge.call_sites[0]["column"]) == (12, 9)


def test_an_implementation_answer_reaches_only_the_implementation():
    graph = _graph()

    _link(graph, [_site(LOUD, 7, 4, IMPLEMENTATION)])

    assert _destinations(graph) == {"Framework.Loud.UseAuditing(App app)"}


def test_a_receiver_names_the_member_it_calls():
    graph = _graph()

    _link(graph, [_site(BAG, 2, 0, RECEIVER, member="Add(int item)")])

    assert _destinations(graph) == {"Framework.Bag.Add(int item)", "Framework.Bag"}


def test_a_receiver_stands_in_only_for_a_call_no_definition_finished():
    graph = _graph()

    _link(graph, [_site(BAG, 2, 0, RECEIVER, member="Add(int item)"), _site(BUILDER, 19, 4)])

    assert "Framework.Builder.UseAuditing(App app)" in _destinations(graph)
    assert "Framework.Bag.Add(int item)" not in _destinations(graph)


def test_an_override_in_a_file_the_engine_never_read_is_an_edge():
    """An engine's dispatch index reads inheritance from its own files; the merged graph holds the rest."""
    graph = _graph()
    site = _site(BUILDER, 19, 4, OVERRIDE, member="Framework.Builder.UseAuditing(App app)")

    linked = _link(graph, [site], analysed=frozenset({HOST, BUILDER}))

    assert _destinations(graph) == {"Framework.Loud.UseAuditing(App app)"}
    assert linked.unresolved == []


def test_an_override_in_a_file_the_engine_read_is_left_to_the_engine():
    """The engine's dispatch index already decided these, by rules the merged graph does not have."""
    graph = _graph()
    site = _site(BUILDER, 19, 4, OVERRIDE, member="Framework.Builder.UseAuditing(App app)")

    _link(graph, [site], analysed=frozenset({HOST, BUILDER, LOUD}))

    assert graph.edges == []


class TestShapesTheEngineKept:
    """A site keeps the shape the engine found it as, so the merged graph finishes it the same way."""

    def _typescript_adapter(self) -> MagicMock:
        adapter = MagicMock()
        adapter.language_id = "typescript"
        adapter.resolves_method_groups = True
        adapter.resolves_collection_initializers = False
        adapter.resolves_iterated_types = False
        adapter.expands_virtual_dispatch = False
        adapter.expands_constructors = False
        return adapter

    def _csharp_adapter(self, collections: bool = False, iterated: bool = False) -> MagicMock:
        adapter = MagicMock()
        adapter.language_id = "csharp"
        adapter.resolves_method_groups = False
        adapter.resolves_collection_initializers = collections
        adapter.resolves_iterated_types = iterated
        adapter.expands_virtual_dispatch = False
        adapter.expands_constructors = True
        return adapter

    def _call_sites(
        self,
        graph: CallGraph,
        changed: Path,
        caller: str,
        adapter: MagicMock,
        answer: Callable[[int], tuple[str, int, int] | None],
    ) -> list[ExternalCallSite]:
        """The sites the engine keeps for *changed*: each answer, by column, in a file it never read."""
        potential = potential_calls(changed, SourceInspector(), adapter, GraphIndex(graph).callable_names)
        sites = []
        for site in potential.call_sites:
            at = answer(site.lsp_column)
            if at is not None:
                kind = potential.definition_kind_at((site.lsp_line, site.lsp_column))
                sites.append(ExternalCallSite(caller, at[0], at[1], at[2], site, kind))
        return sites

    def _link(self, graph: CallGraph, changed: Path, sites: list[ExternalCallSite], adapter: MagicMock) -> None:
        link_external_call_sites(GraphIndex(graph), sites, adapter, SourceInspector(), {str(changed)})

    def test_a_collection_initializer_that_constructs_keeps_its_constructor_edge(self, tmp_path: Path) -> None:
        """``new Bag { 1 }`` is one site with two shapes, and a full build gives it both.

        The engine decides constructor expansion per site, from the source; deciding it from
        the call-site kind instead leaves the ``Add`` edge but no constructor.
        """
        changed = tmp_path / "app.cs"
        changed.write_text("class App\n{\n    void Run()\n    {\n        var bag = new Bag { 1 };\n    }\n}\n")
        bag = tmp_path / "bag.cs"
        graph = CallGraph(language="csharp")
        graph.add_node(Node("app.App.Run", NodeType.METHOD, str(changed), line_start=3, line_end=6, col_start=9))
        graph.add_node(Node("bag.Bag", NodeType.CLASS, str(bag), line_start=1, line_end=4, col_start=6))
        graph.add_node(Node("bag.Bag.Bag", NodeType.CONSTRUCTOR, str(bag), line_start=2, line_end=2, col_start=11))
        graph.add_node(Node("bag.Bag.Add", NodeType.METHOD, str(bag), line_start=3, line_end=3, col_start=9))
        adapter = self._csharp_adapter(collections=True)

        sites = self._call_sites(graph, changed, "app.App.Run", adapter, lambda _: (str(bag), 0, 6))
        self._link(graph, changed, sites, adapter)

        assert ("app.App.Run", "bag.Bag.Add") in _edges(graph)
        assert ("app.App.Run", "bag.Bag.Bag") in _edges(graph)

    def test_a_loop_over_a_construction_keeps_its_constructor_edge(self, tmp_path: Path) -> None:
        """A loop subject can be a construction too, and a full build expands it as one."""
        changed = tmp_path / "app.cs"
        changed.write_text(
            "class App\n{\n    void Run()\n    {\n        foreach (var x in new Bag { 1 }) { }\n    }\n}\n"
        )
        bag = tmp_path / "bag.cs"
        graph = CallGraph(language="csharp")
        graph.add_node(Node("app.App.Run", NodeType.METHOD, str(changed), line_start=3, line_end=6, col_start=9))
        graph.add_node(Node("bag.Bag", NodeType.CLASS, str(bag), line_start=1, line_end=4, col_start=6))
        graph.add_node(Node("bag.Bag.Bag", NodeType.CONSTRUCTOR, str(bag), line_start=2, line_end=2, col_start=11))
        adapter = self._csharp_adapter(iterated=True)

        loops = SourceInspector().find_iterated_expression_sites(changed)
        sites = [ExternalCallSite("app.App.Run", str(bag), 0, 6, site, ITERATED) for site in loops]
        self._link(graph, changed, sites, adapter)

        assert ("app.App.Run", "bag.Bag.Bag") in _edges(graph)

    def test_a_callback_bound_to_a_const_is_a_method_group_target(self, tmp_path: Path) -> None:
        helpers = tmp_path / "helpers.ts"
        helpers.write_text("export const handler = () => 1;\n")
        changed = tmp_path / "app.ts"
        changed.write_text(
            "import { handler } from './helpers';\n\nexport function run() {\n    subscribe(handler);\n}\n"
        )
        graph = CallGraph(language="typescript")
        graph.add_node(Node("app.run", NodeType.FUNCTION, str(changed), line_start=3, line_end=5, col_start=16))
        graph.add_node(Node("helpers.handler", NodeType.VARIABLE, str(helpers), line_start=1, line_end=1, col_start=13))
        adapter = self._typescript_adapter()

        sites = self._call_sites(
            graph, changed, "app.run", adapter, lambda col: (str(helpers), 0, 13) if col == 14 else None
        )
        self._link(graph, changed, sites, adapter)

        assert _edges(graph) == [("app.run", "helpers.handler")]

    def test_a_constant_passed_as_an_argument_is_not_an_edge(self, tmp_path: Path) -> None:
        helpers = tmp_path / "helpers.ts"
        helpers.write_text("export const LIMIT = 5;\n")
        changed = tmp_path / "app.ts"
        changed.write_text("import { LIMIT } from './helpers';\n\nexport function run() {\n    subscribe(LIMIT);\n}\n")
        graph = CallGraph(language="typescript")
        graph.add_node(Node("app.run", NodeType.FUNCTION, str(changed), line_start=3, line_end=5, col_start=16))
        graph.add_node(Node("helpers.LIMIT", NodeType.VARIABLE, str(helpers), line_start=1, line_end=1, col_start=13))
        adapter = self._typescript_adapter()

        sites = self._call_sites(
            graph, changed, "app.run", adapter, lambda col: (str(helpers), 0, 13) if col == 14 else None
        )
        self._link(graph, changed, sites, adapter)

        assert _edges(graph) == []

    def test_a_member_call_that_leaves_the_repository_resolves_through_its_receiver(self, tmp_path: Path) -> None:
        logger_file = tmp_path / "log.ts"
        logger_file.write_text("export const log = { warn(m: string) {} };\n")
        changed = tmp_path / "app.ts"
        changed.write_text("import { log } from './log';\n\nexport function run() {\n    log.warn('x');\n}\n")
        graph = CallGraph(language="typescript")
        graph.add_node(Node("app.run", NodeType.FUNCTION, str(changed), line_start=3, line_end=5, col_start=16))
        graph.add_node(Node("log.log", NodeType.VARIABLE, str(logger_file), line_start=1, line_end=1, col_start=13))
        graph.add_node(Node("log.log.warn", NodeType.METHOD, str(logger_file), line_start=1, line_end=1, col_start=21))
        call = CallSite.from_lsp_position(str(changed), 3, 8)

        site = ExternalCallSite("app.run", str(logger_file), 0, 13, call, RECEIVER, "warn")
        self._link(graph, changed, [site], self._typescript_adapter())

        assert _edges(graph) == [("app.run", "log.log.warn")]

    def test_a_member_reached_through_its_receiver_also_reaches_its_class(self, tmp_path: Path) -> None:
        """The full build adds the class a called method belongs to, whichever route found the method."""
        logger_file = tmp_path / "log.ts"
        logger_file.write_text("export class Log { warn(m: string) {} }\n")
        changed = tmp_path / "app.ts"
        changed.write_text("import { Log } from './log';\n\nexport function run() {\n    Log.warn('x');\n}\n")
        graph = CallGraph(language="typescript")
        graph.add_node(Node("app.run", NodeType.FUNCTION, str(changed), line_start=3, line_end=5, col_start=16))
        graph.add_node(Node("log.Log", NodeType.CLASS, str(logger_file), line_start=1, line_end=1, col_start=13))
        graph.add_node(Node("log.Log.warn", NodeType.METHOD, str(logger_file), line_start=1, line_end=1, col_start=19))
        call = CallSite.from_lsp_position(str(changed), 3, 8)

        site = ExternalCallSite("app.run", str(logger_file), 0, 13, call, RECEIVER, "warn")
        self._link(graph, changed, [site], self._typescript_adapter())

        assert sorted(_edges(graph)) == [("app.run", "log.Log"), ("app.run", "log.Log.warn")]
