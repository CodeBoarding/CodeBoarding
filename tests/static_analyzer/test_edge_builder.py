"""Tests for static_analyzer.engine.edge_builder — both strategies and helpers."""

from pathlib import Path

import pytest
from unittest.mock import MagicMock, patch

from static_analyzer.engine.edge_builder import (
    EdgeMap,
    SymbolIndex,
    _is_valid_edge,
    _build_dispatch_index,
    _override_targets,
    build_edges_via_definitions,
)
from static_analyzer.config import NodeType
from static_analyzer.engine.edge_build_context import EdgeBuildContext
from static_analyzer.engine.models import SymbolInfo
from static_analyzer.engine.source_inspector import SourceInspector
from static_analyzer.engine.symbol_table import SymbolTable

from tests.static_analyzer.test_call_graph_builder import _TestAdapter


class _DefinitionsTestAdapter(_TestAdapter):
    @property
    def resolves_method_groups(self) -> bool:
        return True


def _make_lsp() -> MagicMock:
    lsp = MagicMock()
    lsp.send_definition_batch.return_value = ([], set())
    lsp.send_implementation_batch.return_value = ([], set())
    return lsp


def _make_ctx(lsp: MagicMock | None = None) -> tuple[EdgeBuildContext, _TestAdapter]:
    adapter = _TestAdapter()
    if lsp is None:
        lsp = _make_lsp()
    ctx = EdgeBuildContext(lsp, SymbolTable(adapter), SourceInspector())
    return ctx, adapter


def _sym(
    name: str,
    qname: str,
    kind: int,
    path: str,
    start_line: int,
    start_char: int = 0,
    end_line: int | None = None,
    end_char: int = 100,
    parent_chain: list[tuple[str, int]] | None = None,
) -> SymbolInfo:
    return SymbolInfo(
        name=name,
        qualified_name=qname,
        kind=kind,
        file_path=Path(path),
        start_line=start_line,
        start_char=start_char,
        end_line=end_line if end_line is not None else start_line + 10,
        end_char=end_char,
        parent_chain=parent_chain or [],
    )


# ---------------------------------------------------------------------------
# _is_valid_edge
# ---------------------------------------------------------------------------


class TestIsValidEdge:
    def test_valid_edge(self):
        a = _sym("foo", "a.foo", NodeType.FUNCTION, "/p/a.py", 0)
        b = _sym("bar", "a.bar", NodeType.FUNCTION, "/p/a.py", 20)
        assert _is_valid_edge(a, b) is True

    def test_rejects_same_name(self):
        a = _sym("foo", "a.foo", NodeType.FUNCTION, "/p/a.py", 0)
        assert _is_valid_edge(a, a) is False

    def test_rejects_child_of_caller(self):
        parent = _sym("Cls", "a.Cls", NodeType.CLASS, "/p/a.py", 0)
        child = _sym("method", "a.Cls.method", NodeType.METHOD, "/p/a.py", 5)
        assert _is_valid_edge(parent, child) is False

    def test_rejects_parent_of_target(self):
        child = _sym("method", "a.Cls.method", NodeType.METHOD, "/p/a.py", 5)
        parent = _sym("Cls", "a.Cls", NodeType.CLASS, "/p/a.py", 0)
        assert _is_valid_edge(child, parent) is False

    def test_rejects_same_definition_location(self):
        a = _sym("foo", "a.foo", NodeType.FUNCTION, "/p/a.py", 10, 4)
        b = _sym("bar", "b.bar", NodeType.FUNCTION, "/p/a.py", 10, 4)
        assert _is_valid_edge(a, b) is False

    def test_rejects_same_file_and_line(self):
        a = _sym("foo", "a.foo", NodeType.FUNCTION, "/p/a.py", 10, 0)
        b = _sym("bar", "a.bar", NodeType.FUNCTION, "/p/a.py", 10, 5)
        assert _is_valid_edge(a, b) is False


# ---------------------------------------------------------------------------
# SymbolIndex.resolve
# ---------------------------------------------------------------------------


def _definition(path: str, line: int, char: int) -> dict:
    return {"uri": Path(path).as_uri(), "range": {"start": {"line": line, "character": char}}}


def _index(symbols: list[SymbolInfo], inspector: SourceInspector | None = None) -> SymbolIndex:
    table = SymbolTable(_TestAdapter())
    for sym in symbols:
        table.symbols[sym.qualified_name] = sym
    return SymbolIndex(table, inspector or SourceInspector())


class TestSymbolIndexResolve:
    def test_exact_match_with_location_format(self):
        sym = _sym("foo", "a.foo", NodeType.FUNCTION, "/p/a.py", 10, 4)
        assert _index([sym]).resolve(_definition("/p/a.py", 10, 4)) is sym

    def test_exact_match_with_location_link_format(self):
        sym = _sym("foo", "a.foo", NodeType.FUNCTION, "/p/a.py", 10, 4)
        result = _index([sym]).resolve(
            {
                "targetUri": Path("/p/a.py").as_uri(),
                "targetSelectionRange": {"start": {"line": 10, "character": 4}},
            }
        )
        assert result is sym

    def test_symbol_declared_on_the_line_that_contains_the_position(self, tmp_path: Path):
        source = tmp_path / "a.ts"
        source.write_text("export class Box { hold(item: string) {} }\n")
        method = _sym("hold", "a.Box.hold", NodeType.METHOD, str(source), 0, 19, 0, 41)
        box = _sym("Box", "a.Box", NodeType.CLASS, str(source), 0, 13, 0, 42)
        assert _index([box, method]).resolve(_definition(str(source), 0, 25)) is method

    def test_symbol_whose_declaration_ends_above_the_position_is_not_a_match(self, tmp_path: Path):
        source = tmp_path / "a.py"
        source.write_text("def head():\n    pass\n\n\ndef tail():\n    pass\n")
        head = _sym("head", "a.head", NodeType.FUNCTION, str(source), 0, 4, 1, 8)
        assert _index([head]).resolve(_definition(str(source), 4, 4)) is None

    def test_parameter_line_does_not_bind_to_the_neighbouring_function(self, tmp_path: Path):
        source = tmp_path / "handlers.py"
        source.write_text("def handle(\n    payload,\n):\n    return payload\n")
        function = _sym("handle", "handlers.handle", NodeType.FUNCTION, str(source), 0, 4, 3, 18)
        assert _index([function]).resolve(_definition(str(source), 1, 4)) is None

    def test_local_variable_line_does_not_bind_to_the_neighbouring_function(self, tmp_path: Path):
        source = tmp_path / "calc.py"
        source.write_text("def total(items):\n    subtotal = 0\n    return subtotal\n")
        function = _sym("total", "calc.total", NodeType.FUNCTION, str(source), 0, 4, 2, 20)
        assert _index([function]).resolve(_definition(str(source), 1, 4)) is None

    def test_import_line_does_not_bind_to_the_neighbouring_function(self, tmp_path: Path):
        source = tmp_path / "app.py"
        source.write_text("from lib import helper\n\n\ndef run():\n    return helper()\n")
        function = _sym("run", "app.run", NodeType.FUNCTION, str(source), 3, 4, 4, 20)
        assert _index([function]).resolve(_definition(str(source), 0, 16)) is None

    def test_overload_signature_resolves_to_the_sole_declaration_of_that_name(self, tmp_path: Path):
        source = tmp_path / "teams.ts"
        source.write_text(
            "export function getTeams(id: string): Team[];\n"
            "export function getTeams(id: number): Team[];\n"
            "export function getTeams(id: unknown): Team[] {\n  return [];\n}\n"
        )
        implementation = _sym("getTeams", "teams.getTeams", NodeType.FUNCTION, str(source), 2, 16, 4, 1)
        assert _index([implementation]).resolve(_definition(str(source), 0, 16)) is implementation

    def test_two_declarations_of_the_name_are_ambiguous(self, tmp_path: Path):
        source = tmp_path / "teams.ts"
        source.write_text(
            "export function getTeams(id: string): Team[];\n"
            "export function getTeams() {}\n"
            "class Api { getTeams() {} }\n"
        )
        free = _sym("getTeams", "teams.getTeams", NodeType.FUNCTION, str(source), 1, 16, 1, 29)
        method = _sym("getTeams", "teams.Api.getTeams", NodeType.METHOD, str(source), 2, 12, 2, 26)
        assert _index([free, method]).resolve(_definition(str(source), 0, 16)) is None

    def test_dual_registration_at_one_position_is_one_declaration(self, tmp_path: Path):
        source = tmp_path / "teams.ts"
        source.write_text("export function getTeams(id: string): Team[];\nexport function getTeams() {}\n")
        short = _sym("getTeams", "teams.getTeams", NodeType.FUNCTION, str(source), 1, 16, 1, 29)
        long = _sym("getTeams", "teams.index.getTeams", NodeType.FUNCTION, str(source), 1, 16, 1, 29)
        assert _index([short, long]).resolve(_definition(str(source), 0, 16)) is long

    def test_name_at_definition_only_considers_callables_and_classes(self, tmp_path: Path):
        source = tmp_path / "config.py"
        source.write_text("DEBUG = True\n\n\nDEBUG = False\n")
        first = _sym("DEBUG", "config.DEBUG", NodeType.CONSTANT, str(source), 0, 0, 0, 5)
        assert _index([first]).resolve(_definition(str(source), 3, 0)) is None

    def test_adjacent_line_is_no_longer_a_match(self, tmp_path: Path):
        source = tmp_path / "a.py"
        source.write_text("@decorator\ndef foo():\n    pass\n")
        sym = _sym("foo", "a.foo", NodeType.FUNCTION, str(source), 1, 4, 2, 8)
        assert _index([sym]).resolve(_definition(str(source), 0, 0)) is None

    def test_annotation_line_above_a_method_still_resolves_by_name(self, tmp_path: Path):
        source = tmp_path / "Service.java"
        source.write_text("class Service {\n  @Override\n  public void run() {}\n}\n")
        method = _sym("run()", "Service.run()", NodeType.METHOD, str(source), 2, 14, 2, 22)
        assert _index([method]).resolve(_definition(str(source), 2, 14)) is method

    def test_returns_none_for_invalid_uri(self):
        assert _index([]).resolve({"uri": "invalid-uri", "range": {"start": {"line": 0, "character": 0}}}) is None

    def test_returns_none_for_missing_position(self):
        assert _index([]).resolve({"uri": Path("/p/a.py").as_uri(), "range": {}}) is None

    def test_counts_every_outcome(self, tmp_path: Path):
        source = tmp_path / "teams.ts"
        source.write_text("export function getTeams(id: string): Team[];\nexport function getTeams() {}\n")
        sym = _sym("getTeams", "teams.getTeams", NodeType.FUNCTION, str(source), 1, 16, 1, 29)
        index = _index([sym])
        index.resolve(_definition(str(source), 1, 16))
        index.resolve(_definition(str(source), 1, 20))
        index.resolve(_definition(str(source), 0, 16))
        index.resolve(_definition(str(source), 0, 0))
        assert (index.counts.exact, index.counts.same_line) == (1, 1)
        assert (index.counts.name_at_definition, index.counts.rejected) == (1, 1)


# ---------------------------------------------------------------------------
# build_edges_via_definitions
# ---------------------------------------------------------------------------


class TestBuildEdgesViaDefinitions:
    def test_resolves_call_site_to_edge(self, tmp_path: Path):
        """Definition query resolves a call site -> produces an edge."""
        lsp = _make_lsp()
        ctx, adapter = _make_ctx(lsp)
        st = ctx.symbol_table

        src = tmp_path / "app.py"
        src.write_text("def main():\n    helper()\n\ndef helper():\n    pass\n")

        caller = _sym("main", "app.main", NodeType.FUNCTION, str(src), 0, 4, 1)
        callee = _sym("helper", "app.helper", NodeType.FUNCTION, str(src), 3, 4, 4)
        st._symbols["app.main"] = caller
        st._symbols["app.helper"] = callee
        st._file_symbols[str(src)] = [caller, callee]
        st._primary_file_symbols[str(src)] = [caller, callee]
        st.build_indices()

        # Call sites: main( at (0,4), helper( at (1,4), helper( def at (3,4)
        # helper( at (1,4) resolves to callee at (3,4)
        def def_batch(queries: list) -> tuple[list, set[int]]:
            return [
                (
                    [{"uri": src.as_uri(), "range": {"start": {"line": 3, "character": 4}}}]
                    if line == 1 and col == 4
                    else []
                )
                for _, line, col in queries
            ], set()

        lsp.send_definition_batch.side_effect = def_batch

        edges = build_edges_via_definitions(adapter, ctx, [src])
        assert ("app.main", "app.helper") in edges

    def test_a_definition_in_a_file_this_engine_never_named_is_kept_for_the_merge(self, tmp_path: Path):
        lsp = _make_lsp()
        ctx, adapter = _make_ctx(lsp)
        st = ctx.symbol_table

        src = tmp_path / "modules" / "app.py"
        src.parent.mkdir()
        src.write_text("def main():\n    helper()\n")
        other = tmp_path / "framework" / "lib.py"

        caller = _sym("main", "app.main", NodeType.FUNCTION, str(src), 0, 4, 1)
        st._symbols["app.main"] = caller
        st._file_symbols[str(src)] = [caller]
        st._primary_file_symbols[str(src)] = [caller]
        st.build_indices()

        lsp.send_definition_batch.side_effect = lambda queries: (
            [
                [{"uri": other.as_uri(), "range": {"start": {"line": 7, "character": 4}}}] if line == 1 else []
                for _, line, _ in queries
            ],
            set(),
        )

        edges = build_edges_via_definitions(adapter, ctx, [src])

        assert edges == {}
        assert [(s.caller, s.file, s.line, s.character, s.kind) for s in ctx.external_call_sites] == [
            ("app.main", str(other), 7, 4, "call")
        ]
        assert (ctx.external_call_sites[0].call_site.line, ctx.external_call_sites[0].call_site.column) == (2, 5)

    def test_no_call_sites_produces_empty(self, tmp_path: Path):
        """File with no call sites produces no edges."""
        lsp = _make_lsp()
        ctx, adapter = _make_ctx(lsp)

        src = tmp_path / "empty.py"
        src.write_text("# just a comment\n")

        edges = build_edges_via_definitions(adapter, ctx, [src])
        assert len(edges) == 0

    def test_definition_batch_failure_does_not_crash(self, tmp_path: Path):
        """A failed batch loses its edges without stopping the run.

        Whether that is the right trade is decided on the fail-fast branch; here
        the point is only that the run survives it.
        """
        lsp = _make_lsp()
        ctx, adapter = _make_ctx(lsp)
        st = ctx.symbol_table

        src = tmp_path / "app.py"
        src.write_text("def main():\n    helper()\n")

        caller = _sym("main", "app.main", NodeType.FUNCTION, str(src), 0, 4, 1)
        st._symbols["app.main"] = caller
        st._file_symbols[str(src)] = [caller]
        st._primary_file_symbols[str(src)] = [caller]
        st.build_indices()

        lsp.send_definition_batch.side_effect = Exception("LSP crash")

        assert len(build_edges_via_definitions(adapter, ctx, [src])) == 0

    def test_constructor_adds_parent_class_edge(self, tmp_path: Path):
        """When definition resolves to a constructor, also adds edge to parent class."""
        lsp = _make_lsp()
        ctx, adapter = _make_ctx(lsp)
        st = ctx.symbol_table

        src = tmp_path / "app.py"
        src.write_text("def main():\n    Dog()\n\nclass Dog:\n    def __init__(self):\n        pass\n")

        caller = _sym("main", "app.main", NodeType.FUNCTION, str(src), 0, 4, 1)
        cls = _sym("Dog", "app.Dog", NodeType.CLASS, str(src), 3, 6, 5)
        ctor = _sym(
            "__init__",
            "app.Dog.__init__",
            NodeType.CONSTRUCTOR,
            str(src),
            4,
            8,
            5,
            parent_chain=[("Dog", NodeType.CLASS)],
        )
        st._symbols["app.main"] = caller
        st._symbols["app.Dog"] = cls
        st._symbols["app.Dog.__init__"] = ctor
        st._file_symbols[str(src)] = [caller, cls, ctor]
        st._primary_file_symbols[str(src)] = [caller, cls, ctor]
        st.build_indices()

        # Dog( at (1,4) resolves to __init__ at (4,8); other sites resolve to nothing
        def def_batch(queries: list) -> tuple[list, set[int]]:
            return [
                (
                    [{"uri": src.as_uri(), "range": {"start": {"line": 4, "character": 8}}}]
                    if line == 1 and col == 4
                    else []
                )
                for _, line, col in queries
            ], set()

        lsp.send_definition_batch.side_effect = def_batch
        lsp.send_implementation_batch.return_value = ([[]], set())

        edges = build_edges_via_definitions(adapter, ctx, [src])
        assert ("app.main", "app.Dog.__init__") in edges
        assert ("app.main", "app.Dog") in edges

    def test_implementation_queries_for_polymorphism(self, tmp_path: Path):
        """Implementation queries add edges for polymorphic dispatch."""
        lsp = _make_lsp()
        ctx, adapter = _make_ctx(lsp)
        st = ctx.symbol_table

        src = tmp_path / "app.py"
        src.write_text("def main():\n    speak()\n\ndef speak():\n    pass\n\ndef dog_speak():\n    pass\n")

        caller = _sym("main", "app.main", NodeType.FUNCTION, str(src), 0, 4, 1)
        target = _sym("speak", "app.speak", NodeType.METHOD, str(src), 3, 4, 4)
        impl = _sym("dog_speak", "app.dog_speak", NodeType.METHOD, str(src), 6, 4, 7)
        st._symbols["app.main"] = caller
        st._symbols["app.speak"] = target
        st._symbols["app.dog_speak"] = impl
        st._file_symbols[str(src)] = [caller, target, impl]
        st._primary_file_symbols[str(src)] = [caller, target, impl]
        st.build_indices()

        # speak( at (1,4) resolves to speak def at (3,4); others to nothing
        def def_batch(queries: list) -> tuple[list, set[int]]:
            return [
                (
                    [{"uri": src.as_uri(), "range": {"start": {"line": 3, "character": 4}}}]
                    if line == 1 and col == 4
                    else []
                )
                for _, line, col in queries
            ], set()

        lsp.send_definition_batch.side_effect = def_batch
        # Implementation for speak resolves to dog_speak
        lsp.send_implementation_batch.return_value = (
            [[{"uri": src.as_uri(), "range": {"start": {"line": 6, "character": 4}}}]],
            set(),
        )

        edges = build_edges_via_definitions(adapter, ctx, [src])
        assert ("app.main", "app.speak") in edges
        assert ("app.main", "app.dog_speak") in edges

    def test_handles_implementation_batch_failure(self, tmp_path: Path):
        """Implementation batch failure doesn't crash."""
        lsp = _make_lsp()
        ctx, adapter = _make_ctx(lsp)
        st = ctx.symbol_table

        src = tmp_path / "app.py"
        src.write_text("def main():\n    speak()\n\ndef speak():\n    pass\n")

        caller = _sym("main", "app.main", NodeType.FUNCTION, str(src), 0, 4, 1)
        target = _sym("speak", "app.speak", NodeType.METHOD, str(src), 3, 4, 4)
        st._symbols["app.main"] = caller
        st._symbols["app.speak"] = target
        st._file_symbols[str(src)] = [caller, target]
        st._primary_file_symbols[str(src)] = [caller, target]
        st.build_indices()

        # speak( at (1,4) resolves to speak def at (3,4)
        def def_batch(queries: list) -> tuple[list, set[int]]:
            return [
                (
                    [{"uri": src.as_uri(), "range": {"start": {"line": 3, "character": 4}}}]
                    if line == 1 and col == 4
                    else []
                )
                for _, line, col in queries
            ], set()

        lsp.send_definition_batch.side_effect = def_batch
        lsp.send_implementation_batch.side_effect = Exception("LSP crash")

        edges = build_edges_via_definitions(adapter, ctx, [src])
        # Definition edge still present despite impl failure
        assert ("app.main", "app.speak") in edges


# ---------------------------------------------------------------------------
# Generic call shapes: function-valued targets and the receiver fallback
# ---------------------------------------------------------------------------


def _definitions_ctx(lsp: MagicMock) -> tuple[EdgeBuildContext, _DefinitionsTestAdapter]:
    adapter = _DefinitionsTestAdapter()
    return EdgeBuildContext(lsp, SymbolTable(adapter), SourceInspector()), adapter


def _register(ctx: EdgeBuildContext, path: Path, symbols: list[SymbolInfo]) -> None:
    st = ctx.symbol_table
    for sym in symbols:
        st._symbols[sym.qualified_name] = sym
    st._file_symbols[str(path)] = symbols
    st._primary_file_symbols[str(path)] = symbols
    st.build_indices()


def _answer(lsp: MagicMock, answers: dict[tuple[int, int], tuple[int, int]], path: Path) -> None:
    """Resolve each queried position to the declaration position it maps to."""

    def batch(queries: list) -> tuple[list, set[int]]:
        return [
            (
                [{"uri": path.as_uri(), "range": {"start": {"line": at[0], "character": at[1]}}}]
                if (at := answers.get((line, col))) is not None
                else []
            )
            for _, line, col in queries
        ], set()

    lsp.send_definition_batch.side_effect = batch


class TestFunctionValuedTargets:
    def test_a_callback_bound_to_a_const_is_a_method_group_target(self, tmp_path: Path):
        lsp = _make_lsp()
        ctx, adapter = _definitions_ctx(lsp)
        src = tmp_path / "app.ts"
        src.write_text("const handler = () => 1;\n\nexport function run() {\n    subscribe(handler);\n}\n")

        run = _sym("run", "app.run", NodeType.FUNCTION, str(src), 2, 16, 4)
        handler = _sym("handler", "app.handler", NodeType.VARIABLE, str(src), 0, 6, 0, 23)
        _register(ctx, src, [run, handler])
        _answer(lsp, {(3, 14): (0, 6)}, src)

        assert ("app.run", "app.handler") in build_edges_via_definitions(adapter, ctx, [src])

    def test_a_constant_passed_as_an_argument_is_not_an_edge(self, tmp_path: Path):
        lsp = _make_lsp()
        ctx, adapter = _definitions_ctx(lsp)
        src = tmp_path / "app.ts"
        src.write_text("const LIMIT = 5;\n\nexport function run() {\n    subscribe(LIMIT);\n}\n")

        run = _sym("run", "app.run", NodeType.FUNCTION, str(src), 2, 16, 4)
        limit = _sym("LIMIT", "app.LIMIT", NodeType.VARIABLE, str(src), 0, 6, 0, 15)
        _register(ctx, src, [run, limit])
        _answer(lsp, {(3, 14): (0, 6)}, src)

        assert build_edges_via_definitions(adapter, ctx, [src]) == {}


class TestReceiverMemberFallback:
    def test_a_member_call_that_leaves_the_repository_resolves_through_its_receiver(self, tmp_path: Path):
        lsp = _make_lsp()
        ctx, adapter = _definitions_ctx(lsp)
        src = tmp_path / "app.ts"
        src.write_text("export const log = { warn(m: string) {} };\n\nexport function run() {\n    log.warn('x');\n}\n")

        run = _sym("run", "app.run", NodeType.FUNCTION, str(src), 2, 16, 4)
        log = _sym("log", "app.log", NodeType.VARIABLE, str(src), 0, 13, 0, 41)
        warn = _sym("warn", "app.log.warn", NodeType.METHOD, str(src), 0, 21, 0, 39)
        _register(ctx, src, [run, log, warn])
        # The member resolves nowhere; only the receiver at (3, 4) answers.
        _answer(lsp, {(3, 4): (0, 13)}, src)

        assert ("app.run", "app.log.warn") in build_edges_via_definitions(adapter, ctx, [src])

    def test_a_receiver_that_holds_no_such_member_adds_nothing(self, tmp_path: Path):
        lsp = _make_lsp()
        ctx, adapter = _definitions_ctx(lsp)
        src = tmp_path / "app.ts"
        src.write_text("export const log = { info(m: string) {} };\n\nexport function run() {\n    log.warn('x');\n}\n")

        run = _sym("run", "app.run", NodeType.FUNCTION, str(src), 2, 16, 4)
        log = _sym("log", "app.log", NodeType.VARIABLE, str(src), 0, 13, 0, 41)
        info = _sym("info", "app.log.info", NodeType.METHOD, str(src), 0, 21, 0, 39)
        _register(ctx, src, [run, log, info])
        _answer(lsp, {(3, 4): (0, 13)}, src)

        assert build_edges_via_definitions(adapter, ctx, [src]) == {}

    def test_a_member_call_the_engine_already_resolved_is_not_asked_again(self, tmp_path: Path):
        lsp = _make_lsp()
        ctx, adapter = _definitions_ctx(lsp)
        src = tmp_path / "app.ts"
        src.write_text("export const log = { warn(m: string) {} };\n\nexport function run() {\n    log.warn('x');\n}\n")

        run = _sym("run", "app.run", NodeType.FUNCTION, str(src), 2, 16, 4)
        log = _sym("log", "app.log", NodeType.VARIABLE, str(src), 0, 13, 0, 41)
        warn = _sym("warn", "app.log.warn", NodeType.METHOD, str(src), 0, 21, 0, 39)
        _register(ctx, src, [run, log, warn])
        _answer(lsp, {(3, 8): (0, 21)}, src)

        build_edges_via_definitions(adapter, ctx, [src])
        queried = {(line, col) for call in lsp.send_definition_batch.call_args_list for _, line, col in call.args[0]}
        assert (3, 4) not in queried
