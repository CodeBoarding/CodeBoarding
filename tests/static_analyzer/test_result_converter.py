"""Tests for static_analyzer.engine.result_converter.

Verifies that references produced by convert_to_codeboarding_format have the correct
line numbers, node types, and qualified names.  Also checks that local variables,
parameters, and dual-registration aliases are correctly excluded from references.
"""

from pathlib import Path
from unittest.mock import MagicMock

from static_analyzer.constants import NodeType
from static_analyzer.engine.language_adapter import LanguageAdapter
from static_analyzer.engine.models import CallFlowGraph, LanguageAnalysisResult, SymbolInfo
from static_analyzer.engine.result_converter import _map_symbol_kind, convert_to_codeboarding_format
from static_analyzer.engine.symbol_table import SymbolTable


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_adapter() -> MagicMock:
    """Create a mock LanguageAdapter with realistic behaviour."""
    adapter = MagicMock(spec=LanguageAdapter)
    adapter.language = "Python"
    adapter.is_callable.side_effect = lambda k: k in (NodeType.FUNCTION, NodeType.METHOD, NodeType.CONSTRUCTOR)
    adapter.is_class_like.side_effect = lambda k: k in (
        NodeType.CLASS,
        NodeType.INTERFACE,
        NodeType.STRUCT,
        NodeType.ENUM,
    )
    adapter.is_reference_worthy.side_effect = lambda k: k in (
        {NodeType.FUNCTION, NodeType.METHOD, NodeType.CONSTRUCTOR}
        | {NodeType.CLASS, NodeType.INTERFACE, NodeType.STRUCT, NodeType.ENUM}
        | {NodeType.VARIABLE, NodeType.CONSTANT, NodeType.PROPERTY, NodeType.FIELD, NodeType.ENUM_MEMBER}
    )
    adapter.build_qualified_name.side_effect = lambda fp, name, kind, chain, root, detail="": (
        ".".join(n for n, _ in chain) + "." + name if chain else f"{fp.stem}.{name}"
    )
    adapter.build_reference_key.side_effect = lambda qn: qn
    adapter.should_track_for_edges.side_effect = lambda k: k in (
        NodeType.FUNCTION,
        NodeType.METHOD,
        NodeType.CONSTRUCTOR,
        NodeType.CLASS,
        NodeType.VARIABLE,
        NodeType.CONSTANT,
    )
    return adapter


def _register(st: SymbolTable, symbols: list[dict], file: str = "mod.py", root: str = "/root") -> None:
    """Shorthand for registering a flat list of raw LSP symbol dicts."""
    st.register_symbols(Path(file), symbols, parent_chain=[], project_root=Path(root))
    st.build_indices()


def _lsp_sym(
    name: str,
    kind: int,
    start_line: int,
    end_line: int,
    children: list[dict] | None = None,
) -> dict:
    """Build a minimal LSP DocumentSymbol dict."""
    d: dict = {
        "name": name,
        "kind": kind,
        "range": {
            "start": {"line": start_line, "character": 0},
            "end": {"line": end_line, "character": 0},
        },
        "selectionRange": {
            "start": {"line": start_line, "character": 4},
            "end": {"line": start_line, "character": 4 + len(name)},
        },
    }
    if children:
        d["children"] = children
    return d


def _empty_result(source_files: list[str] | None = None) -> LanguageAnalysisResult:
    return LanguageAnalysisResult(source_files=source_files or [])


# ---------------------------------------------------------------------------
# _map_symbol_kind
# ---------------------------------------------------------------------------


class TestMapSymbolKind:
    def test_known_kinds_map_correctly(self):
        assert _map_symbol_kind(NodeType.FUNCTION) == NodeType.FUNCTION
        assert _map_symbol_kind(NodeType.CLASS) == NodeType.CLASS
        assert _map_symbol_kind(NodeType.METHOD) == NodeType.METHOD
        assert _map_symbol_kind(NodeType.VARIABLE) == NodeType.VARIABLE
        assert _map_symbol_kind(NodeType.CONSTRUCTOR) == NodeType.CONSTRUCTOR
        assert _map_symbol_kind(NodeType.INTERFACE) == NodeType.INTERFACE
        assert _map_symbol_kind(NodeType.PROPERTY) == NodeType.PROPERTY

    def test_unknown_kind_falls_back_to_function(self):
        assert _map_symbol_kind(999) == NodeType.FUNCTION


# ---------------------------------------------------------------------------
# References: line numbers (the +1 conversion from 0-based LSP to 1-based)
# ---------------------------------------------------------------------------


class TestReferenceLineNumbers:
    """Verify that the 0-based LSP lines stored in SymbolInfo are converted
    to 1-based line_start / line_end on the output Node objects."""

    def test_function_line_numbers(self):
        adapter = _make_adapter()
        st = SymbolTable(adapter)
        # LSP reports line 9 (0-based) -> expected 1-based line 10
        _register(st, [_lsp_sym("my_func", NodeType.FUNCTION, start_line=9, end_line=19)])
        result = convert_to_codeboarding_format(st, _empty_result(), adapter)

        refs = result["references"]
        assert len(refs) == 1
        assert refs[0].fully_qualified_name == "mod.my_func"
        assert refs[0].line_start == 10  # 9 + 1
        assert refs[0].line_end == 20  # 19 + 1

    def test_class_and_method_line_numbers(self):
        adapter = _make_adapter()
        st = SymbolTable(adapter)
        _register(
            st,
            [
                _lsp_sym(
                    "MyClass",
                    NodeType.CLASS,
                    start_line=0,
                    end_line=30,
                    children=[
                        _lsp_sym("do_work", NodeType.METHOD, start_line=5, end_line=15),
                        _lsp_sym("helper", NodeType.METHOD, start_line=17, end_line=29),
                    ],
                )
            ],
        )
        result = convert_to_codeboarding_format(st, _empty_result(), adapter)

        refs = {r.fully_qualified_name: r for r in result["references"]}
        # Class itself
        assert "mod.MyClass" in refs
        assert refs["mod.MyClass"].line_start == 1
        assert refs["mod.MyClass"].line_end == 31

        # Methods (primary registration uses parent chain: "MyClass.do_work")
        assert "MyClass.do_work" in refs
        assert refs["MyClass.do_work"].line_start == 6  # 5 + 1
        assert refs["MyClass.do_work"].line_end == 16

        assert "MyClass.helper" in refs
        assert refs["MyClass.helper"].line_start == 18  # 17 + 1
        assert refs["MyClass.helper"].line_end == 30

    def test_zero_based_line_zero_becomes_one(self):
        """Edge case: symbol at the very first line of a file."""
        adapter = _make_adapter()
        st = SymbolTable(adapter)
        _register(st, [_lsp_sym("top", NodeType.FUNCTION, start_line=0, end_line=3)])
        result = convert_to_codeboarding_format(st, _empty_result(), adapter)

        refs = result["references"]
        assert len(refs) == 1
        assert refs[0].line_start == 1
        assert refs[0].line_end == 4


# ---------------------------------------------------------------------------
# References: node types
# ---------------------------------------------------------------------------


class TestReferenceNodeTypes:
    """Verify that the output Node.type matches the original LSP SymbolKind."""

    def test_function_type(self):
        adapter = _make_adapter()
        st = SymbolTable(adapter)
        _register(st, [_lsp_sym("fn", NodeType.FUNCTION, 0, 5)])
        refs = convert_to_codeboarding_format(st, _empty_result(), adapter)["references"]
        assert refs[0].type == NodeType.FUNCTION

    def test_class_type(self):
        adapter = _make_adapter()
        st = SymbolTable(adapter)
        _register(st, [_lsp_sym("Cls", NodeType.CLASS, 0, 10)])
        refs = convert_to_codeboarding_format(st, _empty_result(), adapter)["references"]
        assert refs[0].type == NodeType.CLASS

    def test_method_type(self):
        adapter = _make_adapter()
        st = SymbolTable(adapter)
        _register(
            st,
            [_lsp_sym("Cls", NodeType.CLASS, 0, 20, children=[_lsp_sym("m", NodeType.METHOD, 2, 10)])],
        )
        refs = convert_to_codeboarding_format(st, _empty_result(), adapter)["references"]
        methods = [r for r in refs if r.type == NodeType.METHOD]
        assert len(methods) == 1

    def test_variable_type(self):
        adapter = _make_adapter()
        st = SymbolTable(adapter)
        _register(st, [_lsp_sym("MY_CONST", NodeType.VARIABLE, 0, 0)])
        refs = convert_to_codeboarding_format(st, _empty_result(), adapter)["references"]
        assert refs[0].type == NodeType.VARIABLE

    def test_enum_type(self):
        adapter = _make_adapter()
        st = SymbolTable(adapter)
        _register(st, [_lsp_sym("Color", NodeType.ENUM, 0, 5)])
        refs = convert_to_codeboarding_format(st, _empty_result(), adapter)["references"]
        assert refs[0].type == NodeType.ENUM


# ---------------------------------------------------------------------------
# References: exclusion of local variables / parameters / aliases
# ---------------------------------------------------------------------------


class TestReferenceExclusions:
    """Ensure local variables, parameters, and dual-registration aliases are excluded."""

    def test_local_variable_inside_function_excluded(self):
        """A variable declared inside a function should NOT appear in references."""
        adapter = _make_adapter()
        st = SymbolTable(adapter)
        _register(
            st,
            [
                _lsp_sym(
                    "outer",
                    NodeType.FUNCTION,
                    0,
                    20,
                    children=[_lsp_sym("temp", NodeType.VARIABLE, 5, 5)],
                )
            ],
        )
        result = convert_to_codeboarding_format(st, _empty_result(), adapter)
        ref_names = {r.fully_qualified_name for r in result["references"]}
        # The function itself should be present
        assert "mod.outer" in ref_names
        # The local variable should be excluded (it has a parent chain)
        assert all("temp" not in n for n in ref_names)

    def test_parameter_inside_method_excluded(self):
        """A variable/constant inside a method (parameter-like) should be excluded."""
        adapter = _make_adapter()
        st = SymbolTable(adapter)
        _register(
            st,
            [
                _lsp_sym(
                    "Cls",
                    NodeType.CLASS,
                    0,
                    30,
                    children=[
                        _lsp_sym(
                            "method",
                            NodeType.METHOD,
                            2,
                            20,
                            children=[_lsp_sym("param", NodeType.VARIABLE, 3, 3)],
                        ),
                    ],
                )
            ],
        )
        result = convert_to_codeboarding_format(st, _empty_result(), adapter)
        ref_names = {r.fully_qualified_name for r in result["references"]}
        assert all("param" not in n for n in ref_names)

    def test_module_level_variable_included(self):
        """A top-level variable (no parent chain) should appear in references."""
        adapter = _make_adapter()
        st = SymbolTable(adapter)
        _register(st, [_lsp_sym("GLOBAL_FLAG", NodeType.VARIABLE, 0, 0)])
        result = convert_to_codeboarding_format(st, _empty_result(), adapter)
        ref_names = {r.fully_qualified_name for r in result["references"]}
        assert "mod.GLOBAL_FLAG" in ref_names

    def test_module_level_constant_included(self):
        adapter = _make_adapter()
        st = SymbolTable(adapter)
        _register(st, [_lsp_sym("MAX_RETRIES", NodeType.CONSTANT, 2, 2)])
        result = convert_to_codeboarding_format(st, _empty_result(), adapter)
        ref_names = {r.fully_qualified_name for r in result["references"]}
        assert "mod.MAX_RETRIES" in ref_names

    def test_dual_registration_alias_not_duplicated(self):
        """Dual registration creates aliases but they should not appear as separate references."""
        adapter = _make_adapter()
        st = SymbolTable(adapter)
        _register(
            st,
            [
                _lsp_sym(
                    "Cls",
                    NodeType.CLASS,
                    0,
                    20,
                    children=[_lsp_sym("method", NodeType.METHOD, 5, 15)],
                )
            ],
        )
        result = convert_to_codeboarding_format(st, _empty_result(), adapter)
        # The method should appear once as "Cls.method" (primary), not also as "mod.method" (alias)
        method_refs = [r for r in result["references"] if "method" in r.fully_qualified_name]
        assert len(method_refs) == 1
        assert method_refs[0].fully_qualified_name == "Cls.method"

    def test_non_reference_worthy_kinds_excluded(self):
        """Kinds like MODULE, NAMESPACE, PACKAGE should not become references."""
        adapter = _make_adapter()
        st = SymbolTable(adapter)
        # Manually insert a MODULE symbol (not reference-worthy)
        sym = SymbolInfo(
            name="mymod",
            qualified_name="mod.mymod",
            kind=NodeType.MODULE,
            file_path=Path("mod.py"),
            start_line=0,
            start_char=0,
            end_line=100,
            end_char=0,
        )
        st._symbols["mod.mymod"] = sym
        st._primary_file_symbols.setdefault("mod.py", []).append(sym)
        st.build_indices()

        result = convert_to_codeboarding_format(st, _empty_result(), adapter)
        ref_names = {r.fully_qualified_name for r in result["references"]}
        assert "mod.mymod" not in ref_names


# ---------------------------------------------------------------------------
# References: class-level properties / fields are kept
# ---------------------------------------------------------------------------


class TestClassLevelProperties:
    """Properties whose parent is a class (not a callable) should be kept."""

    def test_class_property_included(self):
        adapter = _make_adapter()
        st = SymbolTable(adapter)
        _register(
            st,
            [
                _lsp_sym(
                    "Cls",
                    NodeType.CLASS,
                    0,
                    30,
                    children=[_lsp_sym("name", NodeType.PROPERTY, 2, 2)],
                )
            ],
        )
        result = convert_to_codeboarding_format(st, _empty_result(), adapter)
        ref_names = {r.fully_qualified_name for r in result["references"]}
        # Class-level property should be present
        assert any("name" in n for n in ref_names)

    def test_property_inside_callable_excluded(self):
        """A property inside a function (e.g. destructured return) should be excluded."""
        adapter = _make_adapter()
        st = SymbolTable(adapter)
        _register(
            st,
            [
                _lsp_sym(
                    "fn",
                    NodeType.FUNCTION,
                    0,
                    20,
                    children=[_lsp_sym("prop", NodeType.PROPERTY, 5, 5)],
                )
            ],
        )
        result = convert_to_codeboarding_format(st, _empty_result(), adapter)
        ref_names = {r.fully_qualified_name for r in result["references"]}
        assert all("prop" not in n for n in ref_names)


# ---------------------------------------------------------------------------
# Call graph nodes and edges
# ---------------------------------------------------------------------------


class TestCallGraphConversion:
    """Verify that the call graph is correctly built from engine results."""

    def test_nodes_and_edges_transferred(self):
        adapter = _make_adapter()
        st = SymbolTable(adapter)
        _register(
            st,
            [
                _lsp_sym("foo", NodeType.FUNCTION, 0, 5),
                _lsp_sym("bar", NodeType.FUNCTION, 7, 12),
            ],
        )
        cfg = CallFlowGraph.from_edge_set({("mod.foo", "mod.bar")})
        result = LanguageAnalysisResult(cfg=cfg)
        out = convert_to_codeboarding_format(st, result, adapter)

        cg = out["call_graph"]
        assert "mod.foo" in cg.nodes
        assert "mod.bar" in cg.nodes
        assert len(cg.edges) == 1

    def test_edge_with_missing_node_skipped(self):
        """If an edge references a symbol not in the symbol table, it should be skipped."""
        adapter = _make_adapter()
        st = SymbolTable(adapter)
        _register(st, [_lsp_sym("foo", NodeType.FUNCTION, 0, 5)])
        cfg = CallFlowGraph.from_edge_set({("mod.foo", "mod.nonexistent")})
        result = LanguageAnalysisResult(cfg=cfg)
        out = convert_to_codeboarding_format(st, result, adapter)

        cg = out["call_graph"]
        assert len(cg.edges) == 0

    def test_edge_participant_nodes_included_even_if_not_graph_type(self):
        """A VARIABLE that participates in edges should appear as a call graph node."""
        adapter = _make_adapter()
        st = SymbolTable(adapter)
        _register(
            st,
            [
                _lsp_sym("handler", NodeType.VARIABLE, 0, 0),
                _lsp_sym("process", NodeType.FUNCTION, 5, 15),
            ],
        )
        cfg = CallFlowGraph.from_edge_set({("mod.handler", "mod.process")})
        result = LanguageAnalysisResult(cfg=cfg)
        out = convert_to_codeboarding_format(st, result, adapter)

        cg = out["call_graph"]
        assert "mod.handler" in cg.nodes
        assert "mod.process" in cg.nodes

    def test_reference_reuses_graph_node(self):
        """If a symbol is both in the graph and reference-worthy, the same Node is reused."""
        adapter = _make_adapter()
        st = SymbolTable(adapter)
        _register(st, [_lsp_sym("foo", NodeType.FUNCTION, 0, 5)])
        cfg = CallFlowGraph(nodes=["mod.foo"], edges=[])
        result = LanguageAnalysisResult(cfg=cfg)
        out = convert_to_codeboarding_format(st, result, adapter)

        graph_node = out["call_graph"].nodes["mod.foo"]
        ref_node = out["references"][0]
        assert graph_node is ref_node


# ---------------------------------------------------------------------------
# Multi-file scenario
# ---------------------------------------------------------------------------


class TestMultiFileReferences:
    """Verify references span multiple files correctly."""

    def test_references_from_two_files(self):
        adapter = _make_adapter()
        st = SymbolTable(adapter)
        st.register_symbols(
            Path("alpha.py"),
            [_lsp_sym("func_a", NodeType.FUNCTION, 0, 10)],
            parent_chain=[],
            project_root=Path("/root"),
        )
        st.register_symbols(
            Path("beta.py"),
            [_lsp_sym("func_b", NodeType.FUNCTION, 3, 8)],
            parent_chain=[],
            project_root=Path("/root"),
        )
        st.build_indices()
        result = convert_to_codeboarding_format(st, _empty_result(), adapter)

        refs = {r.fully_qualified_name: r for r in result["references"]}
        assert "alpha.func_a" in refs
        assert refs["alpha.func_a"].file_path == "alpha.py"
        assert refs["alpha.func_a"].line_start == 1

        assert "beta.func_b" in refs
        assert refs["beta.func_b"].file_path == "beta.py"
        assert refs["beta.func_b"].line_start == 4  # 3 + 1


# ---------------------------------------------------------------------------
# Comprehensive scenario: mixed symbols
# ---------------------------------------------------------------------------


class TestMixedSymbolScenario:
    """A realistic scenario with classes, methods, functions, variables, and locals."""

    def test_comprehensive(self):
        adapter = _make_adapter()
        st = SymbolTable(adapter)
        _register(
            st,
            [
                # Module-level function
                _lsp_sym("main", NodeType.FUNCTION, 0, 8),
                # Module-level constant
                _lsp_sym("VERSION", NodeType.CONSTANT, 10, 10),
                # Class with methods and a local variable
                _lsp_sym(
                    "Engine",
                    NodeType.CLASS,
                    12,
                    50,
                    children=[
                        _lsp_sym("__init__", NodeType.CONSTRUCTOR, 14, 20),
                        _lsp_sym(
                            "run",
                            NodeType.METHOD,
                            22,
                            40,
                            children=[
                                _lsp_sym("result", NodeType.VARIABLE, 25, 25),
                            ],
                        ),
                        _lsp_sym("name", NodeType.FIELD, 42, 42),
                    ],
                ),
            ],
        )

        result = convert_to_codeboarding_format(st, _empty_result(), adapter)
        ref_names = {r.fully_qualified_name for r in result["references"]}

        # Should be present:
        assert "mod.main" in ref_names
        assert "mod.VERSION" in ref_names
        assert "mod.Engine" in ref_names
        assert "Engine.__init__" in ref_names
        assert "Engine.run" in ref_names
        assert "Engine.name" in ref_names

        # Should NOT be present (local variable inside method):
        assert all("result" not in n for n in ref_names)

        # Verify line numbers
        ref_map = {r.fully_qualified_name: r for r in result["references"]}
        assert ref_map["mod.main"].line_start == 1
        assert ref_map["mod.VERSION"].line_start == 11
        assert ref_map["mod.Engine"].line_start == 13
        assert ref_map["Engine.__init__"].line_start == 15
        assert ref_map["Engine.run"].line_start == 23
        assert ref_map["Engine.name"].line_start == 43


# ---------------------------------------------------------------------------
# Output dict structure
# ---------------------------------------------------------------------------


class TestOutputStructure:
    """Verify the output dict has all required keys."""

    def test_all_keys_present(self):
        adapter = _make_adapter()
        st = SymbolTable(adapter)
        _register(st, [_lsp_sym("f", NodeType.FUNCTION, 0, 5)])
        result = _empty_result(source_files=["/root/mod.py"])
        out = convert_to_codeboarding_format(st, result, adapter)

        assert "call_graph" in out
        assert "class_hierarchies" in out
        assert "package_relations" in out
        assert "references" in out
        assert "source_files" in out
        assert "diagnostics" in out
        assert out["diagnostics"] == {}
        assert out["source_files"] == ["/root/mod.py"]
