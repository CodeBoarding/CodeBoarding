"""Tests for static_analyzer.engine.call_graph_builder.CallGraphBuilder."""

from pathlib import Path
from unittest.mock import MagicMock, patch

from static_analyzer.config import Language, NodeType
from static_analyzer.engine.call_graph_builder import CallGraphBuilder
from static_analyzer.engine.edge_builder import EdgeMap, build_edges_via_references
from static_analyzer.engine.language_adapter import LanguageAdapter
from static_analyzer.engine.lsp_constants import DID_OPEN_BATCH_SIZE
from static_analyzer.engine.edge_build_context import EdgeBuildContext
from static_analyzer.engine.models import CallSite, SymbolInfo
from static_analyzer.engine.source_inspector import SourceInspector
from static_analyzer.engine.symbol_table import SymbolTable


class _TestAdapter(LanguageAdapter):
    """Concrete adapter for testing — uses default build_edges (references-based)."""

    @property
    def language(self) -> str:
        return "Python"

    @property
    def language_enum(self):
        return Language.PYTHON

    @property
    def lsp_command(self) -> list[str]:
        return ["pylsp"]


def _make_adapter() -> MagicMock:
    adapter = MagicMock()
    adapter.language_id = "python"
    adapter.is_callable.side_effect = lambda k: k in (NodeType.FUNCTION, NodeType.METHOD, NodeType.CONSTRUCTOR)
    adapter.is_class_like.side_effect = lambda k: k == NodeType.CLASS
    adapter.should_track_for_edges.side_effect = lambda k: k in (
        NodeType.FUNCTION,
        NodeType.METHOD,
        NodeType.CLASS,
        NodeType.VARIABLE,
    )
    adapter.is_reference_worthy.return_value = True
    adapter.build_reference_key.side_effect = lambda qn: qn
    adapter.build_qualified_name.side_effect = lambda fp, name, kind, chain, root, detail="": (
        ".".join(n for n, _ in chain) + "." + name if chain else f"{fp.stem}.{name}"
    )
    adapter.references_batch_size = 50
    adapter.references_per_query_timeout = 0
    adapter.get_all_packages.return_value = {"pkg"}
    adapter.get_package_for_file.return_value = "pkg"
    adapter.build_edges.return_value = set()
    adapter.get_probe_timeout_minimum.return_value = 0
    adapter.probe_before_open = False
    adapter.interleave_did_open_with_symbols = False
    return adapter


def _make_lsp() -> MagicMock:
    lsp = MagicMock()
    lsp.document_symbol.return_value = []
    lsp.send_references_batch.return_value = ([], set())
    lsp.type_hierarchy_prepare.return_value = None
    return lsp


class TestCallGraphBuilderInit:
    def test_creates_symbol_table_and_inspector(self):
        lsp = _make_lsp()
        adapter = _make_adapter()
        builder = CallGraphBuilder(lsp, adapter, Path("/project"))

        assert builder.symbol_table is not None
        assert builder._source_inspector is not None

    def test_resolves_project_root(self):
        lsp = _make_lsp()
        adapter = _make_adapter()
        builder = CallGraphBuilder(lsp, adapter, Path("/project"))
        assert builder._root == Path("/project").resolve()


class TestDiscoverSymbols:
    def test_opens_files_and_queries_symbols(self):
        lsp = _make_lsp()
        adapter = _make_adapter()
        builder = CallGraphBuilder(lsp, adapter, Path("/project"))

        files = [Path("/project/a.py"), Path("/project/b.py")]
        lsp.document_symbol.return_value = []

        builder._discover_symbols(files)

        assert lsp.did_open.call_count == 2
        assert lsp.document_symbol.call_count == 2

    def test_uses_probe_result_for_first_file(self):
        """The probe result from the sync wait should be reused for the first file."""
        lsp = _make_lsp()
        adapter = _make_adapter()
        builder = CallGraphBuilder(lsp, adapter, Path("/project"))

        probe_symbols = [
            {
                "name": "foo",
                "kind": NodeType.FUNCTION,
                "range": {"start": {"line": 0, "character": 0}, "end": {"line": 5, "character": 0}},
                "selectionRange": {"start": {"line": 0, "character": 4}, "end": {"line": 0, "character": 7}},
            }
        ]
        lsp.document_symbol.return_value = probe_symbols

        files = [Path("/project/a.py")]
        builder._discover_symbols(files)

        # document_symbol is called once for the probe, and the probe result is reused
        # for the first file, so no second call
        assert lsp.document_symbol.call_count == 1

    def test_empty_source_files(self):
        lsp = _make_lsp()
        adapter = _make_adapter()
        builder = CallGraphBuilder(lsp, adapter, Path("/project"))

        builder._discover_symbols([])
        lsp.did_open.assert_not_called()

    @patch("static_analyzer.engine.call_graph_builder.time.sleep")
    def test_batches_did_open_calls(self, mock_sleep):
        lsp = _make_lsp()
        adapter = _make_adapter()
        builder = CallGraphBuilder(lsp, adapter, Path("/project"))

        # Create more files than a single batch
        files = [Path(f"/project/file_{i}.py") for i in range(DID_OPEN_BATCH_SIZE + 5)]
        lsp.document_symbol.return_value = []

        builder._discover_symbols(files)

        assert lsp.did_open.call_count == len(files)
        # Should sleep between batches
        mock_sleep.assert_called()

    def test_probe_timeout_scales_linearly_with_file_count(self):
        lsp = _make_lsp()
        adapter = _make_adapter()
        builder = CallGraphBuilder(lsp, adapter, Path("/project"))

        files = [Path(f"/project/file_{i}.py") for i in range(100)]
        lsp.document_symbol.return_value = []

        builder._discover_symbols(files)

        # 60s startup base + 2.0s per file
        probe_call = lsp.document_symbol.call_args_list[0]
        assert probe_call.kwargs.get("timeout") == 260

    def test_probe_timeout_capped_at_maximum(self):
        lsp = _make_lsp()
        adapter = _make_adapter()
        builder = CallGraphBuilder(lsp, adapter, Path("/project"))

        files = [Path(f"/project/file_{i}.py") for i in range(20000)]
        lsp.document_symbol.return_value = []

        builder._discover_symbols(files)

        probe_call = lsp.document_symbol.call_args_list[0]
        assert probe_call.kwargs.get("timeout") == 1800

    def test_interleaves_did_open_with_symbol_queries(self):
        lsp = _make_lsp()
        adapter = _make_adapter()
        adapter.interleave_did_open_with_symbols = True
        builder = CallGraphBuilder(lsp, adapter, Path("/project"))
        files = [Path("/project/a.go"), Path("/project/b.go")]

        builder._discover_symbols(files)

        calls = [(item[0], item.args[0]) for item in lsp.method_calls if item[0] in {"did_open", "document_symbol"}]
        assert calls == [
            ("document_symbol", files[0]),
            ("did_open", files[0]),
            ("document_symbol", files[0]),
            ("did_open", files[1]),
            ("document_symbol", files[1]),
        ]
        assert [item.kwargs.get("timeout") for item in lsp.document_symbol.call_args_list[1:]] == [64, 64]


class TestBuild:
    def test_returns_language_analysis_result(self):
        lsp = _make_lsp()
        adapter = _make_adapter()
        builder = CallGraphBuilder(lsp, adapter, Path("/project"))

        lsp.document_symbol.return_value = [
            {
                "name": "main",
                "kind": NodeType.FUNCTION,
                "range": {"start": {"line": 0, "character": 0}, "end": {"line": 10, "character": 0}},
                "selectionRange": {"start": {"line": 0, "character": 4}, "end": {"line": 0, "character": 8}},
            }
        ]
        lsp.type_hierarchy_prepare.return_value = None

        files = [Path("/project/app.py")]
        result = builder.build(files)

        assert result.source_files == [str(files[0].resolve())]
        assert result.hierarchy is not None
        assert result.cfg is not None
        assert result.package_dependencies is not None

    def test_build_with_no_files(self):
        lsp = _make_lsp()
        adapter = _make_adapter()
        builder = CallGraphBuilder(lsp, adapter, Path("/project"))

        result = builder.build([])

        assert result.source_files == []
        assert len(result.cfg.nodes) == 0
        assert len(result.cfg.edges) == 0


class TestBuildEdges:
    """Tests for the default references-based build_edges on LanguageAdapter."""

    def _make_ctx(self, lsp: MagicMock, adapter: _TestAdapter) -> EdgeBuildContext:
        return EdgeBuildContext(lsp, SymbolTable(adapter), SourceInspector())

    def test_creates_edge_from_reference(self):
        lsp = _make_lsp()
        adapter = _TestAdapter()
        ctx = self._make_ctx(lsp, adapter)

        # Register two symbols
        caller = SymbolInfo("main", "app.main", NodeType.FUNCTION, Path("/project/app.py"), 0, 0, 20, 0)
        callee = SymbolInfo("helper", "app.helper", NodeType.FUNCTION, Path("/project/app.py"), 25, 0, 35, 0)
        st = ctx.symbol_table
        st._symbols["app.main"] = caller
        st._symbols["app.helper"] = callee
        st._file_symbols[str(Path("/project/app.py"))] = [caller, callee]
        st._primary_file_symbols[str(Path("/project/app.py"))] = [caller, callee]
        st.build_indices()

        ref_to_helper = {
            "uri": Path("/project/app.py").as_uri(),
            "range": {
                "start": {"line": 5, "character": 4},
                "end": {"line": 5, "character": 10},
            },
        }
        lsp.send_references_batch.return_value = ([[], [ref_to_helper]], set())

        ctx.source_inspector = MagicMock()
        ctx.source_inspector.is_invocation.return_value = True
        ctx.source_inspector.is_callable_usage.return_value = True

        edge_set = build_edges_via_references(adapter, ctx, [Path("/project/app.py")])

        assert ("app.main", "app.helper") in edge_set

    def test_skips_self_references(self):
        lsp = _make_lsp()
        adapter = _TestAdapter()
        ctx = self._make_ctx(lsp, adapter)

        sym = SymbolInfo("foo", "app.foo", NodeType.FUNCTION, Path("/project/app.py"), 0, 4, 10, 0)
        st = ctx.symbol_table
        st._symbols["app.foo"] = sym
        st._file_symbols[str(Path("/project/app.py"))] = [sym]
        st._primary_file_symbols[str(Path("/project/app.py"))] = [sym]
        st.build_indices()

        ref = {
            "uri": Path("/project/app.py").as_uri(),
            "range": {
                "start": {"line": 0, "character": 4},
                "end": {"line": 0, "character": 7},
            },
        }
        lsp.send_references_batch.return_value = ([[ref]], set())

        edge_set = build_edges_via_references(adapter, ctx, [Path("/project/app.py")])
        assert len(edge_set) == 0

    def test_handles_batch_failure(self):
        lsp = _make_lsp()
        adapter = _TestAdapter()
        ctx = self._make_ctx(lsp, adapter)

        sym = SymbolInfo("foo", "app.foo", NodeType.FUNCTION, Path("/project/app.py"), 0, 0, 10, 0)
        st = ctx.symbol_table
        st._symbols["app.foo"] = sym
        st._file_symbols[str(Path("/project/app.py"))] = [sym]
        st._primary_file_symbols[str(Path("/project/app.py"))] = [sym]
        st.build_indices()

        lsp.send_references_batch.side_effect = Exception("LSP crash")

        edge_set = build_edges_via_references(adapter, ctx, [Path("/project/app.py")])
        assert len(edge_set) == 0


class TestPostprocessEdges:
    """Tests for _postprocess_edges on CallGraphBuilder (constructor expansion, dedup)."""

    SOURCE = """package app;

class Main {
    static void main() {
        Dog dog = new Dog("Rex");
        System.out.println(dog.speak());
    }
}
"""
    CONSTRUCTION = CallSite(file="", line=5, column=23)
    MEMBER_ACCESS = CallSite(file="", line=6, column=32)

    def _builder(self, tmp_path: Path, expands_constructors: bool = True) -> tuple[CallGraphBuilder, Path]:
        """A builder over a real Java file, so the AST can say which site constructs."""
        source = tmp_path / "Main.java"
        source.write_text(self.SOURCE)
        adapter = _make_adapter()
        adapter.expands_constructors = expands_constructors
        builder = CallGraphBuilder(_make_lsp(), adapter, tmp_path)

        caller = SymbolInfo("main", "app.main", NodeType.FUNCTION, source, 3, 4, 7, 0)
        cls = SymbolInfo("Dog", "app.Dog", NodeType.CLASS, source, 25, 0, 50, 0)
        ctor = SymbolInfo("Dog", "app.Dog.Dog(String)", NodeType.CONSTRUCTOR, source, 30, 4, 40, 0)
        ctor.owner_qualified_name = "app.Dog"
        st = builder._symbol_table
        for symbol in (caller, cls, ctor):
            st._symbols[symbol.qualified_name] = symbol
        st._file_symbols[str(source)] = [caller, cls, ctor]
        st._primary_file_symbols[str(source)] = [caller, cls, ctor]
        st.build_indices()
        return builder, source

    def _site(self, template: CallSite, source: Path) -> CallSite:
        return CallSite(file=str(source), line=template.line, column=template.column)

    def test_a_construction_site_reaches_the_constructor(self, tmp_path: Path):
        builder, source = self._builder(tmp_path)
        construction = self._site(self.CONSTRUCTION, source)

        result = builder._postprocess_edges({("app.main", "app.Dog"): [construction]})

        assert ("app.main", "app.Dog") in result
        assert result[("app.main", "app.Dog.Dog(String)")] == [construction]

    def test_a_member_access_site_is_not_a_construction(self, tmp_path: Path):
        """`dog.speak()` resolves to the class as well as the method, and runs no constructor."""
        builder, source = self._builder(tmp_path)
        construction = self._site(self.CONSTRUCTION, source)
        member_access = self._site(self.MEMBER_ACCESS, source)

        result = builder._postprocess_edges({("app.main", "app.Dog"): [construction, member_access]})

        assert result[("app.main", "app.Dog.Dog(String)")] == [construction]

    def test_a_caller_that_only_touches_members_constructs_nothing(self, tmp_path: Path):
        builder, source = self._builder(tmp_path)

        result = builder._postprocess_edges({("app.main", "app.Dog"): [self._site(self.MEMBER_ACCESS, source)]})

        assert ("app.main", "app.Dog.Dog(String)") not in result

    def test_constructor_expansion_preserves_existing_ctor_call_sites(self, tmp_path: Path):
        builder, source = self._builder(tmp_path)
        construction = self._site(self.CONSTRUCTION, source)
        direct = CallSite(file=str(source), line=2, column=5)

        result = builder._postprocess_edges(
            {
                ("app.main", "app.Dog.Dog(String)"): [direct],
                ("app.main", "app.Dog"): [construction],
            }
        )

        assert result[("app.main", "app.Dog.Dog(String)")] == [direct, construction]

    def test_a_constructor_the_server_already_named_is_not_fanned_out(self, tmp_path: Path):
        """`new Dog("Rex")` resolved to one overload must not gain the others beside it."""
        builder, source = self._builder(tmp_path)
        construction = self._site(self.CONSTRUCTION, source)
        other_overload = SymbolInfo("Dog", "app.Dog.Dog()", NodeType.CONSTRUCTOR, source, 33, 4, 35, 0)
        other_overload.owner_qualified_name = "app.Dog"
        st = builder._symbol_table
        st._symbols["app.Dog.Dog()"] = other_overload
        st._primary_file_symbols[str(source)].append(other_overload)
        st._class_to_ctors.clear()
        st.build_indices()

        result = builder._postprocess_edges(
            {
                ("app.main", "app.Dog"): [construction],
                ("app.main", "app.Dog.Dog(String)"): [construction],
            }
        )

        assert ("app.main", "app.Dog.Dog()") not in result

    def test_a_class_edge_with_no_call_site_expands_to_nothing(self, tmp_path: Path):
        builder, _ = self._builder(tmp_path)

        result = builder._postprocess_edges({("app.main", "app.Dog"): []})

        assert ("app.main", "app.Dog.Dog(String)") not in result

    def test_an_adapter_that_does_not_ask_for_expansion_gets_none(self, tmp_path: Path):
        builder, source = self._builder(tmp_path, expands_constructors=False)

        result = builder._postprocess_edges({("app.main", "app.Dog"): [self._site(self.CONSTRUCTION, source)]})

        assert ("app.main", "app.Dog.Dog(String)") not in result


class TestBuildPackageDeps:
    def test_cross_package_dependencies(self):
        lsp = _make_lsp()
        adapter = _make_adapter()
        builder = CallGraphBuilder(lsp, adapter, Path("/project"))

        sym_a = SymbolInfo("foo", "pkg_a.foo", NodeType.FUNCTION, Path("/project/pkg_a/mod.py"), 0, 0, 10, 0)
        sym_b = SymbolInfo("bar", "pkg_b.bar", NodeType.FUNCTION, Path("/project/pkg_b/mod.py"), 0, 0, 10, 0)
        st = builder._symbol_table
        st._symbols["pkg_a.foo"] = sym_a
        st._symbols["pkg_b.bar"] = sym_b

        adapter.get_all_packages.return_value = {"pkg_a", "pkg_b"}
        adapter.get_package_for_file.side_effect = lambda fp, root: "pkg_a" if "pkg_a" in str(fp) else "pkg_b"

        edge_set: EdgeMap = {("pkg_a.foo", "pkg_b.bar"): []}
        source_files = [Path("/project/pkg_a/mod.py"), Path("/project/pkg_b/mod.py")]
        deps = builder._build_package_deps(edge_set, source_files)

        assert "pkg_b" in deps["pkg_a"]["imports"]
        assert "pkg_a" in deps["pkg_b"]["imported_by"]

    def test_same_package_edges_excluded(self):
        lsp = _make_lsp()
        adapter = _make_adapter()
        builder = CallGraphBuilder(lsp, adapter, Path("/project"))

        sym_a = SymbolInfo("foo", "pkg.foo", NodeType.FUNCTION, Path("/project/pkg/a.py"), 0, 0, 10, 0)
        sym_b = SymbolInfo("bar", "pkg.bar", NodeType.FUNCTION, Path("/project/pkg/b.py"), 0, 0, 10, 0)
        st = builder._symbol_table
        st._symbols["pkg.foo"] = sym_a
        st._symbols["pkg.bar"] = sym_b

        adapter.get_all_packages.return_value = {"pkg"}
        adapter.get_package_for_file.return_value = "pkg"

        edge_set: EdgeMap = {("pkg.foo", "pkg.bar"): []}
        deps = builder._build_package_deps(edge_set, [Path("/project/pkg/a.py")])

        assert deps["pkg"]["imports"] == []
        assert deps["pkg"]["imported_by"] == []

    def test_missing_symbols_in_edge_set(self):
        lsp = _make_lsp()
        adapter = _make_adapter()
        builder = CallGraphBuilder(lsp, adapter, Path("/project"))

        adapter.get_all_packages.return_value = {"pkg"}

        edge_set: EdgeMap = {("unknown.foo", "unknown.bar"): []}
        deps = builder._build_package_deps(edge_set, [])

        assert deps["pkg"]["imports"] == []
