"""Tests for static_analyzer.engine.symbol_table.SymbolTable."""

from pathlib import Path
from unittest.mock import MagicMock

from static_analyzer.engine.language_adapter import LanguageAdapter
from static_analyzer.constants import NodeType
from static_analyzer.engine.models import SymbolInfo
from static_analyzer.engine.symbol_table import SymbolTable


def _make_adapter() -> MagicMock:
    """Create a mock adapter with realistic is_callable / is_class_like."""
    adapter = MagicMock(spec=LanguageAdapter)
    adapter.is_callable.side_effect = lambda k: k in (NodeType.FUNCTION, NodeType.METHOD, NodeType.CONSTRUCTOR)
    adapter.is_class_like.side_effect = lambda k: k == NodeType.CLASS
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


def _sym(
    name: str,
    qname: str,
    kind: int,
    fpath: str = "mod.py",
    start_line: int = 0,
    start_char: int = 0,
    end_line: int = 10,
    end_char: int = 0,
    parent_chain: list[tuple[str, int]] | None = None,
) -> SymbolInfo:
    info = SymbolInfo(
        name=name,
        qualified_name=qname,
        kind=kind,
        file_path=Path(fpath),
        start_line=start_line,
        start_char=start_char,
        end_line=end_line,
        end_char=end_char,
    )
    info.parent_chain = parent_chain or []
    return info


# ---- register_symbols ----


class TestRegisterSymbols:
    def test_registers_simple_function(self):
        adapter = _make_adapter()
        st = SymbolTable(adapter)
        symbols = [
            {
                "name": "foo",
                "kind": NodeType.FUNCTION,
                "range": {"start": {"line": 0, "character": 0}, "end": {"line": 5, "character": 0}},
                "selectionRange": {"start": {"line": 0, "character": 4}, "end": {"line": 0, "character": 7}},
            }
        ]
        st.register_symbols(Path("mod.py"), symbols, parent_chain=[], project_root=Path("/root"))
        assert "mod.foo" in st.symbols
        assert st.symbols["mod.foo"].kind == NodeType.FUNCTION

    def test_registers_nested_children(self):
        adapter = _make_adapter()
        st = SymbolTable(adapter)
        symbols = [
            {
                "name": "MyClass",
                "kind": NodeType.CLASS,
                "range": {"start": {"line": 0, "character": 0}, "end": {"line": 20, "character": 0}},
                "selectionRange": {"start": {"line": 0, "character": 6}, "end": {"line": 0, "character": 13}},
                "children": [
                    {
                        "name": "method",
                        "kind": NodeType.METHOD,
                        "range": {"start": {"line": 2, "character": 4}, "end": {"line": 10, "character": 0}},
                        "selectionRange": {"start": {"line": 2, "character": 8}, "end": {"line": 2, "character": 14}},
                    }
                ],
            }
        ]
        st.register_symbols(Path("mod.py"), symbols, parent_chain=[], project_root=Path("/root"))
        assert "mod.MyClass" in st.symbols
        # The child should be registered with parent chain
        assert "MyClass.method" in st.symbols

    def test_promotes_variable_with_callable_children_to_class(self):
        adapter = _make_adapter()
        st = SymbolTable(adapter)
        symbols = [
            {
                "name": "handler",
                "kind": NodeType.VARIABLE,
                "range": {"start": {"line": 0, "character": 0}, "end": {"line": 10, "character": 0}},
                "selectionRange": {"start": {"line": 0, "character": 0}, "end": {"line": 0, "character": 7}},
                "children": [
                    {
                        "name": "process",
                        "kind": NodeType.METHOD,
                        "range": {"start": {"line": 2, "character": 4}, "end": {"line": 5, "character": 0}},
                        "selectionRange": {"start": {"line": 2, "character": 4}, "end": {"line": 2, "character": 11}},
                    }
                ],
            }
        ]
        st.register_symbols(Path("mod.py"), symbols, parent_chain=[], project_root=Path("/root"))
        assert st.symbols["mod.handler"].kind == NodeType.CLASS

    def test_dual_registration_creates_alias(self):
        adapter = _make_adapter()
        st = SymbolTable(adapter)
        symbols = [
            {
                "name": "MyClass",
                "kind": NodeType.CLASS,
                "range": {"start": {"line": 0, "character": 0}, "end": {"line": 20, "character": 0}},
                "selectionRange": {"start": {"line": 0, "character": 6}, "end": {"line": 0, "character": 13}},
                "children": [
                    {
                        "name": "inner_func",
                        "kind": NodeType.FUNCTION,
                        "range": {"start": {"line": 5, "character": 4}, "end": {"line": 10, "character": 0}},
                        "selectionRange": {"start": {"line": 5, "character": 8}, "end": {"line": 5, "character": 18}},
                    }
                ],
            }
        ]
        st.register_symbols(Path("mod.py"), symbols, parent_chain=[], project_root=Path("/root"))
        # Primary registration
        assert "MyClass.inner_func" in st.symbols
        # Dual registration (unqualified alias)
        assert "mod.inner_func" in st.symbols
        # Alias should NOT be in primary_file_symbols
        primary_qnames = {s.qualified_name for s in st.primary_file_symbols.get("mod.py", [])}
        assert "mod.inner_func" not in primary_qnames
        # But should be in file_symbols
        all_qnames = {s.qualified_name for s in st.file_symbols.get("mod.py", [])}
        assert "mod.inner_func" in all_qnames

    def test_skips_symbols_with_empty_name(self):
        adapter = _make_adapter()
        st = SymbolTable(adapter)
        symbols = [
            {
                "name": "",
                "kind": NodeType.FUNCTION,
                "range": {"start": {"line": 0, "character": 0}, "end": {"line": 5, "character": 0}},
            }
        ]
        st.register_symbols(Path("mod.py"), symbols, parent_chain=[], project_root=Path("/root"))
        assert len(st.symbols) == 0


# ---- build_indices ----


class TestBuildIndices:
    def test_builds_file_name_index(self):
        adapter = _make_adapter()
        st = SymbolTable(adapter)
        sym = _sym("foo", "mod.foo", NodeType.FUNCTION)
        st._symbols["mod.foo"] = sym
        st._file_symbols["mod.py"] = [sym]

        st.build_indices()
        assert ("mod.py", "foo") in st._file_name_index
        assert st._file_name_index[("mod.py", "foo")] == [sym]

    def test_builds_class_to_ctor_index(self):
        adapter = _make_adapter()
        st = SymbolTable(adapter)
        ctor_sym = _sym("__init__", "mod.MyClass(__init__)", NodeType.CONSTRUCTOR, start_line=5, end_line=10)
        st._symbols["mod.MyClass(__init__)"] = ctor_sym

        st.build_indices()
        assert "mod.MyClass" in st._class_to_ctors
        assert "mod.MyClass(__init__)" in st._class_to_ctors["mod.MyClass"]

    def test_no_ctor_without_parens(self):
        adapter = _make_adapter()
        st = SymbolTable(adapter)
        method_sym = _sym("do_stuff", "mod.MyClass.do_stuff", NodeType.METHOD)
        st._symbols["mod.MyClass.do_stuff"] = method_sym

        st.build_indices()
        assert "mod.MyClass" not in st._class_to_ctors


# ---- find_containing_symbol ----


class TestFindContainingSymbol:
    def test_finds_innermost_symbol(self):
        adapter = _make_adapter()
        st = SymbolTable(adapter)
        outer = _sym("outer", "mod.outer", NodeType.FUNCTION, start_line=0, end_line=20, end_char=0)
        inner = _sym("inner", "mod.outer.inner", NodeType.FUNCTION, start_line=5, end_line=10, end_char=0)
        st._file_symbols["mod.py"] = [outer, inner]

        result = st.find_containing_symbol(Path("mod.py"), 7, 4)
        assert result is not None
        assert result.qualified_name == "mod.outer.inner"

    def test_returns_none_for_no_match(self):
        adapter = _make_adapter()
        st = SymbolTable(adapter)
        sym = _sym("foo", "mod.foo", NodeType.FUNCTION, start_line=0, end_line=5)
        st._file_symbols["mod.py"] = [sym]

        result = st.find_containing_symbol(Path("mod.py"), 10, 0)
        assert result is None

    def test_returns_none_for_unknown_file(self):
        adapter = _make_adapter()
        st = SymbolTable(adapter)

        result = st.find_containing_symbol(Path("unknown.py"), 0, 0)
        assert result is None

    def test_decorator_attributed_to_method_not_class(self):
        adapter = _make_adapter()
        st = SymbolTable(adapter)
        cls = _sym("C", "mod.C", NodeType.CLASS, start_line=0, end_line=30)
        method = _sym("m", "mod.C.m", NodeType.METHOD, start_line=5, end_line=15)
        st._file_symbols["mod.py"] = [cls, method]

        # Line 3 is a decorator above the method at line 5
        result = st.find_containing_symbol(Path("mod.py"), 3, 4)
        assert result is not None
        assert result.qualified_name == "mod.C.m"


# ---- lift_to_callable ----


class TestLiftToCallable:
    def test_callable_returns_self(self):
        adapter = _make_adapter()
        st = SymbolTable(adapter)
        sym = _sym("foo", "mod.foo", NodeType.FUNCTION)
        st._file_symbols["mod.py"] = [sym]

        result = st.lift_to_callable(sym)
        assert result is sym

    def test_class_returns_self(self):
        adapter = _make_adapter()
        st = SymbolTable(adapter)
        sym = _sym("C", "mod.C", NodeType.CLASS)
        st._file_symbols["mod.py"] = [sym]

        result = st.lift_to_callable(sym)
        assert result is sym

    def test_variable_lifts_to_enclosing_function(self):
        adapter = _make_adapter()
        st = SymbolTable(adapter)
        func = _sym("foo", "mod.foo", NodeType.FUNCTION, start_line=0, end_line=20)
        var = _sym("x", "mod.foo.x", NodeType.VARIABLE, start_line=5, end_line=5)
        st._file_symbols["mod.py"] = [func, var]

        result = st.lift_to_callable(var)
        assert result is not None
        assert result.qualified_name == "mod.foo"

    def test_variable_with_no_enclosing_returns_self(self):
        adapter = _make_adapter()
        st = SymbolTable(adapter)
        var = _sym("x", "mod.x", NodeType.VARIABLE, start_line=0, end_line=0)
        st._file_symbols["mod.py"] = [var]

        result = st.lift_to_callable(var)
        assert result is var


# ---- get_equivalent_names ----


class TestGetEquivalentNames:
    def test_returns_equivalent_names(self):
        adapter = _make_adapter()
        st = SymbolTable(adapter)
        sym1 = _sym("foo", "mod.foo", NodeType.FUNCTION)
        sym2 = _sym("foo", "mod.C.foo", NodeType.FUNCTION)
        st._symbols["mod.foo"] = sym1
        st._symbols["mod.C.foo"] = sym2
        st._file_symbols["mod.py"] = [sym1, sym2]
        st.build_indices()

        eqs = st.get_equivalent_names("mod.foo")
        assert "mod.C.foo" in eqs

    def test_returns_empty_for_unknown(self):
        adapter = _make_adapter()
        st = SymbolTable(adapter)
        assert st.get_equivalent_names("nonexistent") == []


# ---- get_canonical_name ----


class TestGetCanonicalName:
    def test_returns_shortest_equivalent(self):
        adapter = _make_adapter()
        st = SymbolTable(adapter)
        sym1 = _sym("foo", "mod.foo", NodeType.FUNCTION)
        sym2 = _sym("foo", "mod.C.foo", NodeType.FUNCTION)
        st._symbols["mod.foo"] = sym1
        st._symbols["mod.C.foo"] = sym2
        st._file_symbols["mod.py"] = [sym1, sym2]
        st.build_indices()

        assert st.get_canonical_name("mod.C.foo") == "mod.foo"

    def test_returns_self_for_no_equivalents(self):
        adapter = _make_adapter()
        st = SymbolTable(adapter)
        sym1 = _sym("bar", "mod.bar", NodeType.FUNCTION)
        st._symbols["mod.bar"] = sym1
        st._file_symbols["mod.py"] = [sym1]
        st.build_indices()

        assert st.get_canonical_name("mod.bar") == "mod.bar"

    def test_returns_identity_for_unknown(self):
        adapter = _make_adapter()
        st = SymbolTable(adapter)
        assert st.get_canonical_name("missing") == "missing"


# ---- is_local_variable ----


class TestIsLocalVariable:
    def test_module_level_variable_is_not_local(self):
        adapter = _make_adapter()
        st = SymbolTable(adapter)
        sym = _sym("handler", "mod.handler", NodeType.VARIABLE)
        sym.parent_chain = []
        st._file_symbols["mod.py"] = [sym]

        assert st.is_local_variable(sym) is False

    def test_variable_with_parent_is_local(self):
        adapter = _make_adapter()
        st = SymbolTable(adapter)
        sym = _sym("x", "mod.foo.x", NodeType.VARIABLE)
        sym.parent_chain = [("foo", NodeType.FUNCTION)]
        st._file_symbols["mod.py"] = [sym]

        assert st.is_local_variable(sym) is True

    def test_constant_with_parent_is_local(self):
        adapter = _make_adapter()
        st = SymbolTable(adapter)
        sym = _sym("MAX", "mod.foo.MAX", NodeType.CONSTANT)
        sym.parent_chain = [("foo", NodeType.FUNCTION)]

        assert st.is_local_variable(sym) is True

    def test_property_inside_callable_is_local(self):
        adapter = _make_adapter()
        st = SymbolTable(adapter)
        sym = _sym("prop", "mod.foo.prop", NodeType.PROPERTY)
        sym.parent_chain = [("foo", NodeType.FUNCTION)]

        assert st.is_local_variable(sym) is True

    def test_property_inside_class_is_not_local(self):
        adapter = _make_adapter()
        st = SymbolTable(adapter)
        sym = _sym("prop", "mod.C.prop", NodeType.PROPERTY)
        sym.parent_chain = [("C", NodeType.CLASS)]

        assert st.is_local_variable(sym) is False

    def test_function_is_never_local(self):
        adapter = _make_adapter()
        st = SymbolTable(adapter)
        sym = _sym("foo", "mod.foo", NodeType.FUNCTION)
        sym.parent_chain = []

        assert st.is_local_variable(sym) is False

    def test_unqualified_alias_at_same_position_is_local(self):
        """An alias (no parent_chain) at the same position as a parented symbol is local."""
        adapter = _make_adapter()
        st = SymbolTable(adapter)
        # The alias has no parent_chain itself, but shares position with a parented symbol
        alias = _sym("x", "mod.x", NodeType.VARIABLE, start_line=5, start_char=8)
        alias.parent_chain = []
        parented = _sym("x", "mod.foo.x", NodeType.VARIABLE, start_line=5, start_char=8)
        parented.parent_chain = [("foo", NodeType.FUNCTION)]
        st._file_symbols["mod.py"] = [alias, parented]

        assert st.is_local_variable(alias) is True
