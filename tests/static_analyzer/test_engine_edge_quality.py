"""Edge quality in the call graph builder: decoration attribution and alias self-edges."""

from pathlib import Path
from unittest.mock import MagicMock

from static_analyzer.config import NodeType
from static_analyzer.engine.models import SymbolInfo
from static_analyzer.engine.source_inspector import SourceInspector
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


class TestDecorationAttribution:
    """A decoration runs where it is written and belongs to the member below it.

    The gap between the two decides nothing: a module-level call can sit in it and belongs
    to no member at all, so the parse tree, not the distance, has to say which is which.
    """

    def test_a_decorator_is_attributed_to_the_function_it_decorates(self, tmp_path: Path):
        source = tmp_path / "app.py"
        source.write_text("@register\ndef handle():\n    pass\n")

        assert SourceInspector().attribution_position(source, 0, 1) == (1, 4)

    def test_stacked_decorators_are_attributed_to_the_same_function(self, tmp_path: Path):
        source = tmp_path / "app.py"
        source.write_text("@outer\n@inner\ndef handle():\n    pass\n")

        assert SourceInspector().attribution_position(source, 0, 1) == (2, 4)
        assert SourceInspector().attribution_position(source, 1, 1) == (2, 4)

    def test_a_module_level_call_above_a_function_belongs_to_nobody(self, tmp_path: Path):
        source = tmp_path / "app.py"
        source.write_text("initialize()\n\n\ndef handle():\n    pass\n")

        assert SourceInspector().attribution_position(source, 0, 0) == (0, 0)

    def test_an_annotation_is_attributed_to_the_method_it_annotates(self, tmp_path: Path):
        source = tmp_path / "Service.java"
        source.write_text("class Service {\n  @Override\n  public void run() {}\n}\n")

        assert SourceInspector().attribution_position(source, 1, 3) == (2, 14)

    def test_a_position_inside_a_body_speaks_for_itself(self, tmp_path: Path):
        source = tmp_path / "app.py"
        source.write_text("@register\ndef handle():\n    helper()\n")

        assert SourceInspector().attribution_position(source, 2, 4) == (2, 4)


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
