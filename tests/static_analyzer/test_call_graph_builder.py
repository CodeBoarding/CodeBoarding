"""Tests for static_analyzer.engine.call_graph_builder.CallGraphBuilder."""

from pathlib import Path
from unittest.mock import MagicMock, patch

from static_analyzer.engine.call_graph_builder import CallGraphBuilder, DID_OPEN_BATCH_SIZE
from static_analyzer.engine.language_adapter import (
    SYMBOL_KIND_CLASS,
    SYMBOL_KIND_CONSTRUCTOR,
    SYMBOL_KIND_FUNCTION,
    SYMBOL_KIND_METHOD,
    SYMBOL_KIND_VARIABLE,
)
from static_analyzer.engine.models import SymbolInfo


def _make_adapter() -> MagicMock:
    adapter = MagicMock()
    adapter.language_id = "python"
    adapter.is_callable.side_effect = lambda k: k in (SYMBOL_KIND_FUNCTION, SYMBOL_KIND_METHOD, SYMBOL_KIND_CONSTRUCTOR)
    adapter.is_class_like.side_effect = lambda k: k == SYMBOL_KIND_CLASS
    adapter.should_track_for_edges.side_effect = lambda k: k in (
        SYMBOL_KIND_FUNCTION,
        SYMBOL_KIND_METHOD,
        SYMBOL_KIND_CLASS,
        SYMBOL_KIND_VARIABLE,
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
                "kind": SYMBOL_KIND_FUNCTION,
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


class TestBuild:
    def test_returns_language_analysis_result(self):
        lsp = _make_lsp()
        adapter = _make_adapter()
        builder = CallGraphBuilder(lsp, adapter, Path("/project"))

        lsp.document_symbol.return_value = [
            {
                "name": "main",
                "kind": SYMBOL_KIND_FUNCTION,
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
    def test_creates_edge_from_reference(self):
        lsp = _make_lsp()
        adapter = _make_adapter()
        builder = CallGraphBuilder(lsp, adapter, Path("/project"))

        # Register two symbols
        caller = SymbolInfo("main", "app.main", SYMBOL_KIND_FUNCTION, Path("/project/app.py"), 0, 0, 20, 0)
        callee = SymbolInfo("helper", "app.helper", SYMBOL_KIND_FUNCTION, Path("/project/app.py"), 25, 0, 35, 0)
        st = builder._symbol_table
        st._symbols["app.main"] = caller
        st._symbols["app.helper"] = callee
        st._file_symbols[str(Path("/project/app.py"))] = [caller, callee]
        st._primary_file_symbols[str(Path("/project/app.py"))] = [caller, callee]
        st.build_indices()

        # Reference: "helper" is referenced at line 5 inside caller's body.
        # Queries are sent sorted by position: main(0,0) then helper(25,0).
        # The ref for helper (where is helper used?) should be in the second result.
        ref_to_helper = {
            "uri": Path("/project/app.py").as_uri(),
            "range": {
                "start": {"line": 5, "character": 4},
                "end": {"line": 5, "character": 10},
            },
        }
        # First result = refs to main, second = refs to helper
        lsp.send_references_batch.return_value = ([[], [ref_to_helper]], set())

        # Mock source inspector to say this is an invocation
        builder._source_inspector = MagicMock()
        builder._source_inspector.is_invocation.return_value = True
        builder._source_inspector.is_callable_usage.return_value = True

        edge_set = builder._build_edges()

        assert ("app.main", "app.helper") in edge_set

    def test_skips_self_references(self):
        lsp = _make_lsp()
        adapter = _make_adapter()
        builder = CallGraphBuilder(lsp, adapter, Path("/project"))

        sym = SymbolInfo("foo", "app.foo", SYMBOL_KIND_FUNCTION, Path("/project/app.py"), 0, 4, 10, 0)
        st = builder._symbol_table
        st._symbols["app.foo"] = sym
        st._file_symbols[str(Path("/project/app.py"))] = [sym]
        st._primary_file_symbols[str(Path("/project/app.py"))] = [sym]
        st.build_indices()

        # Reference at the same definition location
        ref = {
            "uri": Path("/project/app.py").as_uri(),
            "range": {
                "start": {"line": 0, "character": 4},
                "end": {"line": 0, "character": 7},
            },
        }
        lsp.send_references_batch.return_value = ([[ref]], set())

        edge_set = builder._build_edges()
        assert len(edge_set) == 0

    def test_handles_batch_failure(self):
        lsp = _make_lsp()
        adapter = _make_adapter()
        builder = CallGraphBuilder(lsp, adapter, Path("/project"))

        sym = SymbolInfo("foo", "app.foo", SYMBOL_KIND_FUNCTION, Path("/project/app.py"), 0, 0, 10, 0)
        st = builder._symbol_table
        st._symbols["app.foo"] = sym
        st._file_symbols[str(Path("/project/app.py"))] = [sym]
        st._primary_file_symbols[str(Path("/project/app.py"))] = [sym]
        st.build_indices()

        lsp.send_references_batch.side_effect = Exception("LSP crash")

        edge_set = builder._build_edges()
        assert len(edge_set) == 0

    def test_constructor_expansion(self):
        """When an edge targets a class, constructor edges should be added."""
        lsp = _make_lsp()
        adapter = _make_adapter()
        builder = CallGraphBuilder(lsp, adapter, Path("/project"))

        caller = SymbolInfo("main", "app.main", SYMBOL_KIND_FUNCTION, Path("/project/app.py"), 0, 0, 20, 0)
        cls = SymbolInfo("Dog", "app.Dog", SYMBOL_KIND_CLASS, Path("/project/app.py"), 25, 0, 50, 0)
        ctor = SymbolInfo(
            "__init__", "app.Dog(__init__)", SYMBOL_KIND_CONSTRUCTOR, Path("/project/app.py"), 30, 4, 40, 0
        )
        st = builder._symbol_table
        st._symbols["app.main"] = caller
        st._symbols["app.Dog"] = cls
        st._symbols["app.Dog(__init__)"] = ctor
        file_key = str(Path("/project/app.py"))
        st._file_symbols[file_key] = [caller, cls, ctor]
        st._primary_file_symbols[file_key] = [caller, cls, ctor]
        st.build_indices()

        # Reference: Dog is referenced at line 5 inside caller's body.
        # Positions sorted: main(0,0), Dog(25,0), __init__(30,4)
        # Dog's reference result should include the call site in main
        ref_to_dog = {
            "uri": Path("/project/app.py").as_uri(),
            "range": {
                "start": {"line": 5, "character": 4},
                "end": {"line": 5, "character": 7},
            },
        }
        # 3 queries: main, Dog, __init__; only Dog has a reference
        lsp.send_references_batch.return_value = ([[], [ref_to_dog], []], set())

        builder._source_inspector = MagicMock()
        builder._source_inspector.is_invocation.return_value = True

        edge_set = builder._build_edges()

        assert ("app.main", "app.Dog") in edge_set
        assert ("app.main", "app.Dog(__init__)") in edge_set


class TestBuildPackageDeps:
    def test_cross_package_dependencies(self):
        lsp = _make_lsp()
        adapter = _make_adapter()
        builder = CallGraphBuilder(lsp, adapter, Path("/project"))

        sym_a = SymbolInfo("foo", "pkg_a.foo", SYMBOL_KIND_FUNCTION, Path("/project/pkg_a/mod.py"), 0, 0, 10, 0)
        sym_b = SymbolInfo("bar", "pkg_b.bar", SYMBOL_KIND_FUNCTION, Path("/project/pkg_b/mod.py"), 0, 0, 10, 0)
        st = builder._symbol_table
        st._symbols["pkg_a.foo"] = sym_a
        st._symbols["pkg_b.bar"] = sym_b

        adapter.get_all_packages.return_value = {"pkg_a", "pkg_b"}
        adapter.get_package_for_file.side_effect = lambda fp, root: "pkg_a" if "pkg_a" in str(fp) else "pkg_b"

        edge_set = {("pkg_a.foo", "pkg_b.bar")}
        source_files = [Path("/project/pkg_a/mod.py"), Path("/project/pkg_b/mod.py")]
        deps = builder._build_package_deps(edge_set, source_files)

        assert "pkg_b" in deps["pkg_a"]["imports"]
        assert "pkg_a" in deps["pkg_b"]["imported_by"]

    def test_same_package_edges_excluded(self):
        lsp = _make_lsp()
        adapter = _make_adapter()
        builder = CallGraphBuilder(lsp, adapter, Path("/project"))

        sym_a = SymbolInfo("foo", "pkg.foo", SYMBOL_KIND_FUNCTION, Path("/project/pkg/a.py"), 0, 0, 10, 0)
        sym_b = SymbolInfo("bar", "pkg.bar", SYMBOL_KIND_FUNCTION, Path("/project/pkg/b.py"), 0, 0, 10, 0)
        st = builder._symbol_table
        st._symbols["pkg.foo"] = sym_a
        st._symbols["pkg.bar"] = sym_b

        adapter.get_all_packages.return_value = {"pkg"}
        adapter.get_package_for_file.return_value = "pkg"

        edge_set = {("pkg.foo", "pkg.bar")}
        deps = builder._build_package_deps(edge_set, [Path("/project/pkg/a.py")])

        assert deps["pkg"]["imports"] == []
        assert deps["pkg"]["imported_by"] == []

    def test_missing_symbols_in_edge_set(self):
        lsp = _make_lsp()
        adapter = _make_adapter()
        builder = CallGraphBuilder(lsp, adapter, Path("/project"))

        adapter.get_all_packages.return_value = {"pkg"}

        edge_set = {("unknown.foo", "unknown.bar")}
        deps = builder._build_package_deps(edge_set, [])

        assert deps["pkg"]["imports"] == []


class TestPerFileTimeoutBudget:
    def test_skips_file_after_max_timeouts(self):
        lsp = _make_lsp()
        adapter = _make_adapter()
        adapter.references_batch_size = 1  # One query per batch to control timeout tracking
        builder = CallGraphBuilder(lsp, adapter, Path("/project"))

        # Create 5 symbols in the same file
        file_path = Path("/project/slow.py")
        file_key = str(file_path)
        syms = []
        for i in range(5):
            sym = SymbolInfo(f"func_{i}", f"slow.func_{i}", SYMBOL_KIND_FUNCTION, file_path, i * 10, 0, i * 10 + 5, 0)
            syms.append(sym)
            builder._symbol_table._symbols[sym.qualified_name] = sym

        builder._symbol_table._file_symbols[file_key] = syms
        builder._symbol_table._primary_file_symbols[file_key] = syms
        builder._symbol_table.build_indices()

        # Every query times out
        lsp.send_references_batch.return_value = ([[]], {0})

        edge_set = builder._build_edges()

        # After 3 timeouts, remaining symbols should be skipped
        # Total calls should be 3 (MAX_TIMEOUTS_PER_FILE), not 5
        assert lsp.send_references_batch.call_count == 3
        assert len(edge_set) == 0
