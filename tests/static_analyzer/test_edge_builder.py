"""Tests for static_analyzer.engine.edge_builder — both strategies and helpers."""

from pathlib import Path
from unittest.mock import MagicMock

from static_analyzer.engine.edge_builder import (
    _best_candidate,
    _is_valid_edge,
    _resolve_definition_to_symbol,
    build_edges_via_definitions,
    build_edges_via_references,
)
from static_analyzer.constants import NodeType
from static_analyzer.engine.edge_build_context import EdgeBuildContext
from static_analyzer.engine.models import SymbolInfo
from static_analyzer.engine.source_inspector import SourceInspector
from static_analyzer.engine.symbol_table import SymbolTable

from tests.static_analyzer.test_call_graph_builder import _TestAdapter


def _make_lsp() -> MagicMock:
    lsp = MagicMock()
    lsp.send_references_batch.return_value = ([], set())
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
# _resolve_definition_to_symbol
# ---------------------------------------------------------------------------


class TestResolveDefinitionToSymbol:
    def test_exact_match_with_location_format(self):
        sym = _sym("foo", "a.foo", NodeType.FUNCTION, "/p/a.py", 10, 4)
        pos_to_sym = {(str(Path("/p/a.py")), 10, 4): sym}
        result = _resolve_definition_to_symbol(
            {"uri": Path("/p/a.py").as_uri(), "range": {"start": {"line": 10, "character": 4}}},
            pos_to_sym,
            {},
        )
        assert result is sym

    def test_exact_match_with_location_link_format(self):
        sym = _sym("foo", "a.foo", NodeType.FUNCTION, "/p/a.py", 10, 4)
        pos_to_sym = {(str(Path("/p/a.py")), 10, 4): sym}
        result = _resolve_definition_to_symbol(
            {
                "targetUri": Path("/p/a.py").as_uri(),
                "targetSelectionRange": {"start": {"line": 10, "character": 4}},
            },
            pos_to_sym,
            {},
        )
        assert result is sym

    def test_fuzzy_match_on_same_line(self):
        sym = _sym("foo", "a.foo", NodeType.FUNCTION, "/p/a.py", 10, 4)
        line_to_syms = {(str(Path("/p/a.py")), 10): [sym]}
        result = _resolve_definition_to_symbol(
            {"uri": Path("/p/a.py").as_uri(), "range": {"start": {"line": 10, "character": 0}}},
            {},
            line_to_syms,
        )
        assert result is sym

    def test_fuzzy_match_on_adjacent_line(self):
        sym = _sym("foo", "a.foo", NodeType.FUNCTION, "/p/a.py", 11, 4)
        line_to_syms = {(str(Path("/p/a.py")), 11): [sym]}
        result = _resolve_definition_to_symbol(
            {"uri": Path("/p/a.py").as_uri(), "range": {"start": {"line": 10, "character": 0}}},
            {},
            line_to_syms,
        )
        assert result is sym

    def test_returns_none_for_invalid_uri(self):
        result = _resolve_definition_to_symbol(
            {"uri": "invalid-uri", "range": {"start": {"line": 0, "character": 0}}},
            {},
            {},
        )
        assert result is None

    def test_returns_none_when_no_match(self):
        result = _resolve_definition_to_symbol(
            {"uri": Path("/p/a.py").as_uri(), "range": {"start": {"line": 100, "character": 0}}},
            {},
            {},
        )
        assert result is None


# ---------------------------------------------------------------------------
# _best_candidate
# ---------------------------------------------------------------------------


class TestBestCandidate:
    def test_prefers_callable_over_class(self):
        cls = _sym("Foo", "a.Foo", NodeType.CLASS, "/p/a.py", 0)
        method = _sym("foo", "a.Foo.foo", NodeType.METHOD, "/p/a.py", 5)
        assert _best_candidate([cls, method]) is method

    def test_prefers_class_over_variable(self):
        var = _sym("x", "a.x", NodeType.VARIABLE, "/p/a.py", 0)
        cls = _sym("Foo", "a.Foo", NodeType.CLASS, "/p/a.py", 0)
        assert _best_candidate([var, cls]) is cls

    def test_prefers_longest_qualified_name(self):
        short = _sym("foo", "a.foo", NodeType.FUNCTION, "/p/a.py", 0)
        long = _sym("foo", "a.b.c.foo", NodeType.FUNCTION, "/p/a.py", 0)
        assert _best_candidate([short, long]) is long

    def test_empty_list(self):
        assert _best_candidate([]) is None

    def test_single_variable(self):
        var = _sym("x", "a.x", NodeType.VARIABLE, "/p/a.py", 0)
        assert _best_candidate([var]) is var


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

    def test_no_call_sites_produces_empty(self, tmp_path: Path):
        """File with no call sites produces no edges."""
        lsp = _make_lsp()
        ctx, adapter = _make_ctx(lsp)

        src = tmp_path / "empty.py"
        src.write_text("# just a comment\n")

        edges = build_edges_via_definitions(adapter, ctx, [src])
        assert len(edges) == 0

    def test_handles_definition_batch_failure(self, tmp_path: Path):
        """Definition batch failure doesn't crash."""
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

        edges = build_edges_via_definitions(adapter, ctx, [src])
        assert len(edges) == 0

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
# build_edges_via_references (additional coverage beyond test_call_graph_builder)
# ---------------------------------------------------------------------------


class TestBuildEdgesViaReferencesExtra:
    def test_filters_class_non_invocations(self):
        """References to a class that aren't invocations are filtered out."""
        lsp = _make_lsp()
        ctx, adapter = _make_ctx(lsp)
        st = ctx.symbol_table

        caller = _sym("main", "app.main", NodeType.FUNCTION, "/project/app.py", 0, 0, 20)
        cls = _sym("Dog", "app.Dog", NodeType.CLASS, "/project/app.py", 25, 0, 50)
        st._symbols["app.main"] = caller
        st._symbols["app.Dog"] = cls
        st._file_symbols[str(Path("/project/app.py"))] = [caller, cls]
        st._primary_file_symbols[str(Path("/project/app.py"))] = [caller, cls]
        st.build_indices()

        # Reference to Dog that is NOT an invocation (e.g., type annotation)
        ref = {
            "uri": Path("/project/app.py").as_uri(),
            "range": {"start": {"line": 5, "character": 4}, "end": {"line": 5, "character": 7}},
        }
        lsp.send_references_batch.return_value = ([[], [ref]], set())

        ctx.source_inspector = MagicMock()
        ctx.source_inspector.is_invocation.return_value = False  # Not a call

        edges = build_edges_via_references(adapter, ctx, [Path("/project/app.py")])
        assert ("app.main", "app.Dog") not in edges

    def test_skips_nested_symbol_edges(self):
        """Edges where the target is nested inside the caller are skipped."""
        lsp = _make_lsp()
        ctx, adapter = _make_ctx(lsp)
        st = ctx.symbol_table

        outer = _sym("outer", "app.outer", NodeType.FUNCTION, "/project/app.py", 0, 0, 20)
        inner = _sym("inner", "app.outer.inner", NodeType.FUNCTION, "/project/app.py", 5, 4, 10)
        st._symbols["app.outer"] = outer
        st._symbols["app.outer.inner"] = inner
        st._file_symbols[str(Path("/project/app.py"))] = [outer, inner]
        st._primary_file_symbols[str(Path("/project/app.py"))] = [outer, inner]
        st.build_indices()

        # Reference to inner from within outer
        ref = {
            "uri": Path("/project/app.py").as_uri(),
            "range": {"start": {"line": 3, "character": 4}, "end": {"line": 3, "character": 9}},
        }
        lsp.send_references_batch.return_value = ([[], [ref]], set())

        ctx.source_inspector = MagicMock()
        ctx.source_inspector.is_invocation.return_value = True

        edges = build_edges_via_references(adapter, ctx, [Path("/project/app.py")])
        # inner is nested inside outer, so edge should be skipped
        assert ("app.outer", "app.outer.inner") not in edges

    def test_empty_symbols_no_crash(self):
        """Empty symbol table produces no edges and no crash."""
        lsp = _make_lsp()
        ctx, adapter = _make_ctx(lsp)
        edges = build_edges_via_references(adapter, ctx, [Path("/project/app.py")])
        assert len(edges) == 0

    def test_skips_error_producing_files_in_subsequent_batches(self):
        """When a file produces LSP errors, its symbols are skipped in later batches."""
        lsp = _make_lsp()
        # Use a mock adapter with batch_size=1 so each symbol is its own batch.
        # Symbols sort as: bad.bad_fn1, bad.bad_fn2, good.good_fn
        # Batch 1: bad_fn1 -> error -> bad.py added to skip_files
        # Batch 2: bad_fn2 -> skipped (bad.py in skip_files)
        # Batch 3: good_fn -> queried normally
        adapter = MagicMock()
        adapter.should_track_for_edges.side_effect = lambda k: k in (NodeType.FUNCTION, NodeType.METHOD)
        adapter.is_class_like.return_value = False
        adapter.is_callable.return_value = True
        adapter.references_batch_size = 1
        adapter.references_per_query_timeout = 0

        st = SymbolTable(_TestAdapter())
        ctx = EdgeBuildContext(lsp, st, SourceInspector())

        good_func = _sym("good_fn", "good.good_fn", NodeType.FUNCTION, "/project/good.py", 0, 0, 10)
        bad_func1 = _sym("bad_fn1", "bad.bad_fn1", NodeType.FUNCTION, "/project/bad.py", 0, 0, 10)
        bad_func2 = _sym("bad_fn2", "bad.bad_fn2", NodeType.FUNCTION, "/project/bad.py", 15, 0, 25)

        st._symbols["good.good_fn"] = good_func
        st._symbols["bad.bad_fn1"] = bad_func1
        st._symbols["bad.bad_fn2"] = bad_func2
        for key in [str(Path("/project/good.py")), str(Path("/project/bad.py"))]:
            st._file_symbols[key] = []
            st._primary_file_symbols[key] = []
        st._file_symbols[str(Path("/project/good.py"))] = [good_func]
        st._primary_file_symbols[str(Path("/project/good.py"))] = [good_func]
        st._file_symbols[str(Path("/project/bad.py"))] = [bad_func1, bad_func2]
        st._primary_file_symbols[str(Path("/project/bad.py"))] = [bad_func1, bad_func2]
        st.build_indices()

        def mock_refs_with_errors(queries, per_query_timeout=0):
            error_indices: set[int] = set()
            results: list[list[dict]] = []
            for i, (fp, _, _) in enumerate(queries):
                if "bad.py" in str(fp):
                    results.append([])
                    error_indices.add(i)
                else:
                    results.append([])
            return results, error_indices

        lsp.send_references_batch.side_effect = mock_refs_with_errors

        edges = build_edges_via_references(adapter, ctx, [Path("/project/good.py"), Path("/project/bad.py")])

        # Collect all files that were actually queried via LSP
        all_queried_files: list[str] = []
        for call_args in lsp.send_references_batch.call_args_list:
            for fp, _, _ in call_args[0][0]:
                all_queried_files.append(str(fp))

        # bad_fn1 was queried (error discovered), but bad_fn2 should be skipped
        bad_queries = [f for f in all_queried_files if "bad.py" in f]
        assert len(bad_queries) == 1, f"Expected 1 query for bad.py, got {len(bad_queries)}"
        # good.py should still be queried
        good_queries = [f for f in all_queried_files if "good.py" in f]
        assert len(good_queries) == 1
        assert len(edges) == 0
