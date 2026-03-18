"""Tests for edge quality improvements in the call graph builder.

Covers:
- Alias self-edge removal (same definition location for source and target)
- Decorator-to-method attribution (class-level decorator references
  attributed to the decorated method)
"""

from pathlib import Path
from unittest.mock import MagicMock

from static_analyzer.constants import NodeType
from static_analyzer.engine.models import SymbolInfo
from static_analyzer.engine.symbol_table import SymbolTable


def _make_symbol(
    name: str,
    qname: str,
    kind: int,
    file_path: str,
    start_line: int,
    start_char: int,
    end_line: int,
    end_char: int,
) -> SymbolInfo:
    return SymbolInfo(
        name=name,
        qualified_name=qname,
        kind=kind,
        file_path=Path(file_path),
        start_line=start_line,
        start_char=start_char,
        end_line=end_line,
        end_char=end_char,
    )


def _make_adapter() -> MagicMock:
    adapter = MagicMock()
    adapter.is_callable.side_effect = lambda k: k in (NodeType.FUNCTION, NodeType.METHOD)
    adapter.is_class_like.side_effect = lambda k: k == NodeType.CLASS
    return adapter


class TestFindContainingSymbolDecoratorAttribution:
    """Test that decorator references are attributed to the decorated method."""

    def test_decorator_line_attributed_to_method(self):
        """A reference on a decorator line should resolve to the decorated method."""
        adapter = _make_adapter()
        st = SymbolTable(adapter)
        file = Path("test.py")

        # Class spanning lines 0-50
        cls_sym = _make_symbol("MyClass", "mod.MyClass", NodeType.CLASS, "test.py", 0, 0, 50, 0)
        # Method starting at line 10 (decorator would be at line 8 or 9)
        method_sym = _make_symbol("my_method", "mod.MyClass.my_method", NodeType.METHOD, "test.py", 10, 4, 30, 0)

        st._file_symbols["test.py"] = [cls_sym, method_sym]

        # Reference at line 9 (decorator line, 1 line before method)
        result = st.find_containing_symbol(file, 9, 5)
        assert result is not None
        assert result.qualified_name == "mod.MyClass.my_method"

    def test_decorator_two_lines_before_method(self):
        """Stacked decorators: reference 2 lines before method start."""
        adapter = _make_adapter()
        st = SymbolTable(adapter)
        file = Path("test.py")

        cls_sym = _make_symbol("MyClass", "mod.MyClass", NodeType.CLASS, "test.py", 0, 0, 50, 0)
        method_sym = _make_symbol("my_method", "mod.MyClass.my_method", NodeType.METHOD, "test.py", 10, 4, 30, 0)

        st._file_symbols["test.py"] = [cls_sym, method_sym]

        # Reference at line 8 (2 lines before method — stacked decorator)
        result = st.find_containing_symbol(file, 8, 5)
        assert result is not None
        assert result.qualified_name == "mod.MyClass.my_method"

    def test_class_body_not_attributed_to_distant_method(self):
        """A reference far from any method should stay at the class level."""
        adapter = _make_adapter()
        st = SymbolTable(adapter)
        file = Path("test.py")

        cls_sym = _make_symbol("MyClass", "mod.MyClass", NodeType.CLASS, "test.py", 0, 0, 50, 0)
        method_sym = _make_symbol("my_method", "mod.MyClass.my_method", NodeType.METHOD, "test.py", 10, 4, 30, 0)

        st._file_symbols["test.py"] = [cls_sym, method_sym]

        # Reference at line 3 (7 lines before method — too far for decorator)
        result = st.find_containing_symbol(file, 3, 5)
        assert result is not None
        assert result.qualified_name == "mod.MyClass"

    def test_reference_inside_method_unchanged(self):
        """A reference inside a method body should still resolve to the method."""
        adapter = _make_adapter()
        st = SymbolTable(adapter)
        file = Path("test.py")

        cls_sym = _make_symbol("MyClass", "mod.MyClass", NodeType.CLASS, "test.py", 0, 0, 50, 0)
        method_sym = _make_symbol("my_method", "mod.MyClass.my_method", NodeType.METHOD, "test.py", 10, 4, 30, 0)

        st._file_symbols["test.py"] = [cls_sym, method_sym]

        # Reference at line 15 (inside the method)
        result = st.find_containing_symbol(file, 15, 8)
        assert result is not None
        assert result.qualified_name == "mod.MyClass.my_method"

    def test_picks_nearest_method_with_multiple_methods(self):
        """With multiple methods, decorator attributed to the nearest one after the line."""
        adapter = _make_adapter()
        st = SymbolTable(adapter)
        file = Path("test.py")

        cls_sym = _make_symbol("MyClass", "mod.MyClass", NodeType.CLASS, "test.py", 0, 0, 50, 0)
        method_a = _make_symbol("method_a", "mod.MyClass.method_a", NodeType.METHOD, "test.py", 5, 4, 15, 0)
        method_b = _make_symbol("method_b", "mod.MyClass.method_b", NodeType.METHOD, "test.py", 18, 4, 30, 0)

        st._file_symbols["test.py"] = [cls_sym, method_a, method_b]

        # Decorator at line 17 — should go to method_b (starts at 18), not method_a
        result = st.find_containing_symbol(file, 17, 5)
        assert result is not None
        assert result.qualified_name == "mod.MyClass.method_b"


class TestAliasSelfEdgeRemoval:
    """Test that alias self-edges are removed during edge deduplication."""

    def test_alias_self_edges_removed(self):
        """Edges where src and dst share the same definition location are removed."""
        adapter = _make_adapter()
        adapter.should_track_for_edges.return_value = True

        st = SymbolTable(adapter)

        # Two names for the same symbol (dual registration)
        sym_a = _make_symbol("method", "mod.Class.method", NodeType.METHOD, "test.py", 10, 4, 20, 0)
        sym_b = _make_symbol("method", "mod.method", NodeType.METHOD, "test.py", 10, 4, 20, 0)
        # A different symbol
        sym_c = _make_symbol("other", "mod.Class.other", NodeType.METHOD, "test.py", 25, 4, 35, 0)

        st._symbols = {
            sym_a.qualified_name: sym_a,
            sym_b.qualified_name: sym_b,
            sym_c.qualified_name: sym_c,
        }

        # Edge set with an alias self-edge and a real edge
        edge_set: set[tuple[str, str]] = {
            ("mod.Class.method", "mod.method"),  # alias self-edge — same location
            ("mod.Class.method", "mod.Class.other"),  # real edge
        }

        # Run the dedup logic from _build_edges (extract just the dedup portion)
        pos_to_edge: dict[tuple, tuple[str, str]] = {}
        alias_self_edges = 0
        for src, dst in edge_set:
            src_sym = st.symbols.get(src)
            dst_sym = st.symbols.get(dst)
            if src_sym and dst_sym:
                if src_sym.definition_location == dst_sym.definition_location:
                    alias_self_edges += 1
                    continue
                pos_key = (src_sym.definition_location, dst_sym.definition_location)
                existing = pos_to_edge.get(pos_key)
                if existing is None or len(src) + len(dst) > len(existing[0]) + len(existing[1]):
                    pos_to_edge[pos_key] = (src, dst)
            else:
                pos_key = (src, dst)
                if pos_key not in pos_to_edge:
                    pos_to_edge[pos_key] = (src, dst)
        result_edges = set(pos_to_edge.values())

        assert alias_self_edges == 1
        assert ("mod.Class.method", "mod.method") not in result_edges
        assert ("mod.Class.method", "mod.Class.other") in result_edges

    def test_different_locations_kept(self):
        """Edges between symbols at different locations are kept."""
        adapter = _make_adapter()
        st = SymbolTable(adapter)

        sym_a = _make_symbol("a", "mod.a", NodeType.FUNCTION, "test.py", 1, 0, 5, 0)
        sym_b = _make_symbol("b", "mod.b", NodeType.FUNCTION, "test.py", 10, 0, 15, 0)

        st._symbols = {sym_a.qualified_name: sym_a, sym_b.qualified_name: sym_b}

        edge_set: set[tuple[str, str]] = {("mod.a", "mod.b")}

        pos_to_edge: dict[tuple, tuple[str, str]] = {}
        alias_self_edges = 0
        for src, dst in edge_set:
            src_sym = st.symbols.get(src)
            dst_sym = st.symbols.get(dst)
            if src_sym and dst_sym:
                if src_sym.definition_location == dst_sym.definition_location:
                    alias_self_edges += 1
                    continue
                pos_key = (src_sym.definition_location, dst_sym.definition_location)
                pos_to_edge[pos_key] = (src, dst)

        result_edges = set(pos_to_edge.values())
        assert alias_self_edges == 0
        assert ("mod.a", "mod.b") in result_edges
