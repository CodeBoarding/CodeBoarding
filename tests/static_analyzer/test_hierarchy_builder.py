"""Tests for static_analyzer.engine.hierarchy_builder.HierarchyBuilder."""

from pathlib import Path
from unittest.mock import MagicMock

from static_analyzer.engine.hierarchy_builder import HierarchyBuilder
from static_analyzer.constants import NodeType
from static_analyzer.engine.lsp_client import MethodNotFoundError
from static_analyzer.engine.models import SymbolInfo
from static_analyzer.engine.source_inspector import SourceInspector
from static_analyzer.engine.symbol_table import SymbolTable

# Use absolute path so file_path.as_uri() works and uri_to_path() round-trips
MOD_PATH = Path("/tmp/test_project/mod.py")
MOD_PHP_PATH = Path("/tmp/test_project/mod.php")


def _sym(
    name: str,
    qname: str,
    kind: int,
    fpath: Path = MOD_PATH,
    start_line: int = 0,
    start_char: int = 0,
    end_line: int = 10,
    end_char: int = 0,
) -> SymbolInfo:
    return SymbolInfo(
        name=name,
        qualified_name=qname,
        kind=kind,
        file_path=fpath,
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


def _setup_symbol_table(adapter: MagicMock, class_symbols: list[SymbolInfo]) -> SymbolTable:
    st = SymbolTable(adapter)
    for sym in class_symbols:
        st._symbols[sym.qualified_name] = sym
        file_key = str(sym.file_path)
        st._file_symbols.setdefault(file_key, []).append(sym)
        st._primary_file_symbols.setdefault(file_key, []).append(sym)
    return st


class TestBuildWithTypeHierarchy:
    """Tests for hierarchy building using LSP type hierarchy."""

    def test_builds_hierarchy_from_lsp_supertypes(self):
        adapter = _make_adapter()
        parent = _sym("Animal", "mod.Animal", NodeType.CLASS, start_line=0)
        child = _sym("Dog", "mod.Dog", NodeType.CLASS, start_line=10)
        st = _setup_symbol_table(adapter, [parent, child])

        lsp = MagicMock()
        lsp.type_hierarchy_prepare.side_effect = lambda fp, line, char: [{"name": "stub", "uri": fp.as_uri()}]
        lsp.type_hierarchy_supertypes.side_effect = lambda item: [
            {"name": "Animal", "uri": MOD_PATH.as_uri(), "selectionRange": {"start": {"line": 0}}}
        ]
        lsp.type_hierarchy_subtypes.return_value = []

        si = SourceInspector()
        builder = HierarchyBuilder(lsp, st, si, adapter)
        hierarchy = builder.build()

        assert "mod.Animal" in hierarchy["mod.Dog"]["superclasses"]
        assert "mod.Dog" in hierarchy["mod.Animal"]["subclasses"]

    def test_builds_hierarchy_from_lsp_subtypes(self):
        adapter = _make_adapter()
        parent = _sym("Animal", "mod.Animal", NodeType.CLASS, start_line=0)
        child = _sym("Cat", "mod.Cat", NodeType.CLASS, start_line=10)
        st = _setup_symbol_table(adapter, [parent, child])

        lsp = MagicMock()
        lsp.type_hierarchy_prepare.side_effect = lambda fp, line, char: [{"name": "stub", "uri": fp.as_uri()}]
        lsp.type_hierarchy_supertypes.return_value = []
        lsp.type_hierarchy_subtypes.side_effect = lambda item: [
            {"name": "Cat", "uri": MOD_PATH.as_uri(), "selectionRange": {"start": {"line": 10}}}
        ]

        si = SourceInspector()
        builder = HierarchyBuilder(lsp, st, si, adapter)
        hierarchy = builder.build()

        assert "mod.Cat" in hierarchy["mod.Animal"]["subclasses"]
        assert "mod.Animal" in hierarchy["mod.Cat"]["superclasses"]

    def test_no_duplicates_in_hierarchy(self):
        adapter = _make_adapter()
        parent = _sym("A", "mod.A", NodeType.CLASS, start_line=0)
        child = _sym("B", "mod.B", NodeType.CLASS, start_line=10)
        st = _setup_symbol_table(adapter, [parent, child])

        lsp = MagicMock()
        lsp.type_hierarchy_prepare.side_effect = lambda fp, line, char: [{"name": "stub", "uri": fp.as_uri()}]
        lsp.type_hierarchy_supertypes.side_effect = lambda item: [
            {"name": "A", "uri": MOD_PATH.as_uri(), "selectionRange": {"start": {"line": 0}}}
        ]
        lsp.type_hierarchy_subtypes.side_effect = lambda item: [
            {"name": "B", "uri": MOD_PATH.as_uri(), "selectionRange": {"start": {"line": 10}}}
        ]

        si = SourceInspector()
        builder = HierarchyBuilder(lsp, st, si, adapter)
        hierarchy = builder.build()

        assert hierarchy["mod.B"]["superclasses"].count("mod.A") == 1
        assert hierarchy["mod.A"]["subclasses"].count("mod.B") == 1

    def test_method_not_found_stops_iteration(self):
        adapter = _make_adapter()
        cls1 = _sym("A", "mod.A", NodeType.CLASS, start_line=0)
        cls2 = _sym("B", "mod.B", NodeType.CLASS, start_line=10)
        st = _setup_symbol_table(adapter, [cls1, cls2])

        lsp = MagicMock()
        lsp.type_hierarchy_prepare.side_effect = MethodNotFoundError("not supported")

        si = SourceInspector()
        builder = HierarchyBuilder(lsp, st, si, adapter)
        hierarchy = builder.build()

        assert hierarchy["mod.A"]["superclasses"] == []
        assert hierarchy["mod.B"]["superclasses"] == []

    def test_method_not_found_on_supertypes_is_handled(self):
        adapter = _make_adapter()
        cls = _sym("A", "mod.A", NodeType.CLASS, start_line=0)
        st = _setup_symbol_table(adapter, [cls])

        lsp = MagicMock()
        lsp.type_hierarchy_prepare.return_value = [{"name": "stub"}]
        lsp.type_hierarchy_supertypes.side_effect = MethodNotFoundError("not supported")
        lsp.type_hierarchy_subtypes.return_value = []

        si = SourceInspector()
        builder = HierarchyBuilder(lsp, st, si, adapter)
        hierarchy = builder.build()

        assert hierarchy["mod.A"]["superclasses"] == []

    def test_empty_prepare_result_skipped(self):
        adapter = _make_adapter()
        cls = _sym("A", "mod.A", NodeType.CLASS, start_line=0)
        st = _setup_symbol_table(adapter, [cls])

        lsp = MagicMock()
        lsp.type_hierarchy_prepare.return_value = []
        lsp.type_hierarchy_supertypes.return_value = []
        lsp.type_hierarchy_subtypes.return_value = []

        si = SourceInspector()
        builder = HierarchyBuilder(lsp, st, si, adapter)
        hierarchy = builder.build()

        assert hierarchy["mod.A"]["superclasses"] == []


class TestBuildWithSourceInference:
    """Tests for hierarchy building via source code inference (fallback)."""

    def test_infers_python_single_inheritance(self):
        adapter = _make_adapter()
        parent = _sym("Animal", "mod.Animal", NodeType.CLASS, start_line=0)
        child = _sym("Dog", "mod.Dog", NodeType.CLASS, start_line=5)
        st = _setup_symbol_table(adapter, [parent, child])

        lsp = MagicMock()
        lsp.type_hierarchy_prepare.return_value = None

        si = MagicMock(spec=SourceInspector)
        si.get_source_line.side_effect = lambda fp, line: {
            0: "class Animal:",
            5: "class Dog(Animal):",
        }.get(line)

        builder = HierarchyBuilder(lsp, st, si, adapter)
        hierarchy = builder.build()

        assert "mod.Animal" in hierarchy["mod.Dog"]["superclasses"]
        assert "mod.Dog" in hierarchy["mod.Animal"]["subclasses"]

    def test_infers_python_multiple_inheritance(self):
        adapter = _make_adapter()
        base1 = _sym("A", "mod.A", NodeType.CLASS, start_line=0)
        base2 = _sym("B", "mod.B", NodeType.CLASS, start_line=5)
        child = _sym("C", "mod.C", NodeType.CLASS, start_line=10)
        st = _setup_symbol_table(adapter, [base1, base2, child])

        lsp = MagicMock()
        lsp.type_hierarchy_prepare.return_value = None

        si = MagicMock(spec=SourceInspector)
        si.get_source_line.side_effect = lambda fp, line: {
            0: "class A:",
            5: "class B:",
            10: "class C(A, B):",
        }.get(line)

        builder = HierarchyBuilder(lsp, st, si, adapter)
        hierarchy = builder.build()

        assert "mod.A" in hierarchy["mod.C"]["superclasses"]
        assert "mod.B" in hierarchy["mod.C"]["superclasses"]

    def test_infers_php_extends(self):
        adapter = _make_adapter()
        parent = _sym("Base", "mod.Base", NodeType.CLASS, fpath=MOD_PHP_PATH, start_line=0)
        child = _sym("Child", "mod.Child", NodeType.CLASS, fpath=MOD_PHP_PATH, start_line=5)
        st = _setup_symbol_table(adapter, [parent, child])

        lsp = MagicMock()
        lsp.type_hierarchy_prepare.return_value = None

        si = MagicMock(spec=SourceInspector)
        si.get_source_line.side_effect = lambda fp, line: {
            0: "class Base {",
            5: "class Child extends Base {",
        }.get(line)

        builder = HierarchyBuilder(lsp, st, si, adapter)
        hierarchy = builder.build()

        assert "mod.Base" in hierarchy["mod.Child"]["superclasses"]

    def test_infers_php_implements(self):
        adapter = _make_adapter()
        iface = _sym("Speakable", "mod.Speakable", NodeType.CLASS, fpath=MOD_PHP_PATH, start_line=0)
        child = _sym("Dog", "mod.Dog", NodeType.CLASS, fpath=MOD_PHP_PATH, start_line=5)
        st = _setup_symbol_table(adapter, [iface, child])

        lsp = MagicMock()
        lsp.type_hierarchy_prepare.return_value = None

        si = MagicMock(spec=SourceInspector)
        si.get_source_line.side_effect = lambda fp, line: {
            0: "interface Speakable {",
            5: "class Dog implements Speakable {",
        }.get(line)

        builder = HierarchyBuilder(lsp, st, si, adapter)
        hierarchy = builder.build()

        assert "mod.Speakable" in hierarchy["mod.Dog"]["superclasses"]

    def test_skips_metaclass_keyword_arg(self):
        adapter = _make_adapter()
        cls = _sym("Meta", "mod.Meta", NodeType.CLASS, start_line=0)
        child = _sym("Model", "mod.Model", NodeType.CLASS, start_line=5)
        st = _setup_symbol_table(adapter, [cls, child])

        lsp = MagicMock()
        lsp.type_hierarchy_prepare.return_value = None

        si = MagicMock(spec=SourceInspector)
        si.get_source_line.side_effect = lambda fp, line: {
            0: "class Meta:",
            5: "class Model(metaclass=ABCMeta):",
        }.get(line)

        builder = HierarchyBuilder(lsp, st, si, adapter)
        hierarchy = builder.build()

        assert hierarchy["mod.Model"]["superclasses"] == []

    def test_source_line_none_skips_class(self):
        adapter = _make_adapter()
        cls = _sym("A", "mod.A", NodeType.CLASS, start_line=0)
        st = _setup_symbol_table(adapter, [cls])

        lsp = MagicMock()
        lsp.type_hierarchy_prepare.return_value = None

        si = MagicMock(spec=SourceInspector)
        si.get_source_line.return_value = None

        builder = HierarchyBuilder(lsp, st, si, adapter)
        hierarchy = builder.build()

        assert hierarchy["mod.A"]["superclasses"] == []


class TestResolveTypeHierarchyItem:
    def test_resolves_by_name_and_line(self):
        adapter = _make_adapter()
        sym = _sym("Dog", "mod.Dog", NodeType.CLASS, start_line=10)
        st = _setup_symbol_table(adapter, [sym])

        lsp = MagicMock()
        si = SourceInspector()
        builder = HierarchyBuilder(lsp, st, si, adapter)

        item = {
            "name": "Dog",
            "uri": MOD_PATH.as_uri(),
            "selectionRange": {"start": {"line": 10}},
        }
        result = builder._resolve_type_hierarchy_item(item)
        assert result == "mod.Dog"

    def test_resolves_by_name_within_one_line(self):
        adapter = _make_adapter()
        sym = _sym("Dog", "mod.Dog", NodeType.CLASS, start_line=10)
        st = _setup_symbol_table(adapter, [sym])

        lsp = MagicMock()
        si = SourceInspector()
        builder = HierarchyBuilder(lsp, st, si, adapter)

        item = {
            "name": "Dog",
            "uri": MOD_PATH.as_uri(),
            "selectionRange": {"start": {"line": 11}},
        }
        result = builder._resolve_type_hierarchy_item(item)
        assert result == "mod.Dog"

    def test_falls_back_to_name_and_kind(self):
        adapter = _make_adapter()
        sym = _sym("Dog", "mod.Dog", NodeType.CLASS, start_line=10)
        st = _setup_symbol_table(adapter, [sym])

        lsp = MagicMock()
        si = SourceInspector()
        builder = HierarchyBuilder(lsp, st, si, adapter)

        item = {
            "name": "Dog",
            "uri": MOD_PATH.as_uri(),
            "selectionRange": {"start": {"line": 100}},
        }
        result = builder._resolve_type_hierarchy_item(item)
        assert result == "mod.Dog"

    def test_returns_none_for_non_file_uri(self):
        adapter = _make_adapter()
        st = _setup_symbol_table(adapter, [])

        lsp = MagicMock()
        si = SourceInspector()
        builder = HierarchyBuilder(lsp, st, si, adapter)

        item = {
            "name": "Dog",
            "uri": "https://example.com",
            "selectionRange": {"start": {"line": 0}},
        }
        result = builder._resolve_type_hierarchy_item(item)
        assert result is None

    def test_returns_none_for_unknown_symbol(self):
        adapter = _make_adapter()
        st = _setup_symbol_table(adapter, [])

        lsp = MagicMock()
        si = SourceInspector()
        builder = HierarchyBuilder(lsp, st, si, adapter)

        item = {
            "name": "Unknown",
            "uri": MOD_PATH.as_uri(),
            "selectionRange": {"start": {"line": 0}},
        }
        result = builder._resolve_type_hierarchy_item(item)
        assert result is None
