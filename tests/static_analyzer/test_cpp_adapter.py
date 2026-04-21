"""Tests for the C++ language adapter."""

from pathlib import Path

import pytest

from static_analyzer.constants import NodeType
from static_analyzer.engine.adapters import get_adapter
from static_analyzer.engine.adapters.cpp_adapter import (
    CppAdapter,
    _normalize_cpp_parent,
    _strip_template_args,
)


class TestCppAdapterProperties:
    """Basic adapter property tests."""

    def test_language(self):
        assert CppAdapter().language == "Cpp"

    def test_language_id(self):
        assert CppAdapter().language_id == "cpp"

    def test_lsp_command(self):
        assert CppAdapter().lsp_command == ["clangd"]

    def test_file_extensions_cover_cpp_and_headers(self):
        exts = set(CppAdapter().file_extensions)
        assert {".cpp", ".cc", ".cxx"}.issubset(exts)
        assert {".hpp", ".hh", ".h"}.issubset(exts)

    def test_registry_returns_cpp_adapter(self):
        adapter = get_adapter("Cpp")
        assert isinstance(adapter, CppAdapter)

    def test_references_per_query_timeout_nonzero(self):
        """Non-zero gates the Phase-1.5 warmup probe that lets clangd build
        its background index before Phase 2 fans out cross-TU queries.
        """
        assert CppAdapter().references_per_query_timeout > 0

    def test_phase1_request_timeout_uses_probe_timeout(self):
        """Phase 1 per-file ``document_symbol`` must use ``probe_timeout`` —
        Windows clangd can block a single request for minutes during index
        warm-up and the 60s default would false-fail the analysis.
        """
        probe = 1670
        assert CppAdapter().phase1_request_timeout(probe) == probe

    def test_namespaces_are_reference_worthy(self):
        """Namespaces should appear in the reference map (mirrors C#/PHP)."""
        assert CppAdapter().is_reference_worthy(NodeType.NAMESPACE) is True


class TestCompilationDatabaseGuard:
    """``get_lsp_command`` must reject projects without a compile DB.

    Without a ``compile_commands.json`` / ``compile_flags.txt``, clangd
    silently returns empty cross-file references. Fail fast with actionable
    install instructions instead.
    """

    def test_raises_when_no_compilation_database(self, tmp_path: Path) -> None:
        with pytest.raises(RuntimeError, match=r"compile_commands\.json"):
            CppAdapter().get_lsp_command(tmp_path)

    def test_accepts_compile_flags_txt_at_root(self, tmp_path: Path) -> None:
        (tmp_path / "compile_flags.txt").write_text("-std=c++17\n")
        cmd = CppAdapter().get_lsp_command(tmp_path)
        assert cmd
        assert any("clangd" in part for part in cmd)

    def test_accepts_compile_commands_at_root(self, tmp_path: Path) -> None:
        (tmp_path / "compile_commands.json").write_text("[]")
        cmd = CppAdapter().get_lsp_command(tmp_path)
        assert cmd

    def test_accepts_compile_commands_in_build_subdir(self, tmp_path: Path) -> None:
        (tmp_path / "build").mkdir()
        (tmp_path / "build" / "compile_commands.json").write_text("[]")
        cmd = CppAdapter().get_lsp_command(tmp_path)
        assert cmd

    def test_accepts_cmake_build_debug(self, tmp_path: Path) -> None:
        (tmp_path / "cmake-build-debug").mkdir()
        (tmp_path / "cmake-build-debug" / "compile_commands.json").write_text("[]")
        cmd = CppAdapter().get_lsp_command(tmp_path)
        assert cmd


class TestStripTemplateArgs:
    """Template-argument stripping for qualified-name normalization.

    Specializations should collapse to the template name so LSP references
    match the primary template's symbol.
    """

    def test_drops_simple_template(self):
        assert _strip_template_args("vector<int>") == "vector"

    def test_drops_nested_templates(self):
        assert _strip_template_args("map<string, vector<int>>") == "map"

    def test_preserves_scope_qualifiers(self):
        assert _strip_template_args("std::vector<int>") == "std::vector"

    def test_preserves_operator_less_than(self):
        """``operator<`` is not a template — the ``<`` after ``operator`` must survive."""
        assert _strip_template_args("operator<") == "operator<"

    def test_preserves_operator_shift(self):
        assert _strip_template_args("operator<<") == "operator<<"

    def test_preserves_three_way_comparison(self):
        """C++20 ``operator<=>`` must NOT have its ``<=>`` treated as a
        template-arg block — the old balanced-bracket check erroneously
        stripped it to ``operator``.
        """
        assert _strip_template_args("operator<=>") == "operator<=>"

    def test_preserves_qualified_operator(self):
        """``Foo::operator<=>`` keeps the operator even with scope prefix."""
        assert _strip_template_args("Foo::operator<=>") == "Foo::operator<=>"

    def test_preserves_operator_with_template_class_parent(self):
        """The operator-preservation must not mask real template stripping.
        ``Foo<T>::operator<=>`` keeps the operator AND strips the class template args.
        """
        assert _strip_template_args("Foo<T>::operator<=>") == "Foo::operator<=>"

    def test_preserves_operator_equals(self):
        assert _strip_template_args("operator==") == "operator=="

    def test_preserves_operator_new(self):
        """Keyword operators (``new``, ``delete``) are preserved by the
        same path — don't let the identifier run into adjacent tokens.
        """
        assert _strip_template_args("operator new") == "operator new"

    def test_unbalanced_input_passes_through(self):
        """Malformed inputs shouldn't silently lose characters."""
        assert _strip_template_args("foo<bar") == "foo<bar"


class TestNormalizeCppParent:
    """Parent-chain normalization for ``build_qualified_name``."""

    def test_flattens_scope_operator(self):
        """``::`` becomes ``.`` (the project's universal delimiter)."""
        assert _normalize_cpp_parent("foo::bar") == "foo.bar"

    def test_drops_template_args(self):
        assert _normalize_cpp_parent("Foo<T>") == "Foo"

    def test_handles_template_and_scope_together(self):
        assert _normalize_cpp_parent("std::vector<int>") == "std.vector"


class TestBuildQualifiedName:
    """Qualified-name construction from clangd parent chains.

    C++ declarations and definitions live in separate files (``.hpp`` /
    ``.cpp``). Qualified names must be file-independent so cross-file
    references resolve correctly.
    """

    def _call(
        self,
        file_path: Path,
        symbol: str,
        parent_chain: list[tuple[str, int]],
        project_root: Path,
    ) -> str:
        return CppAdapter().build_qualified_name(
            file_path=file_path,
            symbol_name=symbol,
            symbol_kind=int(NodeType.METHOD),
            parent_chain=parent_chain,
            project_root=project_root,
        )

    def test_namespace_class_method(self, tmp_path: Path) -> None:
        """``foo::Bar::baz`` in a .cpp file uses the namespace chain, not the path."""
        src = tmp_path / "src" / "foo.cpp"
        src.parent.mkdir(parents=True)
        src.touch()
        result = self._call(src, "baz", [("foo", int(NodeType.NAMESPACE)), ("Bar", int(NodeType.CLASS))], tmp_path)
        assert result == "foo.Bar.baz"

    def test_same_name_in_header_and_source(self, tmp_path: Path) -> None:
        """The declaration (.hpp) and definition (.cpp) must produce identical names."""
        (tmp_path / "include").mkdir()
        (tmp_path / "src").mkdir()
        header = tmp_path / "include" / "foo.hpp"
        source = tmp_path / "src" / "foo.cpp"
        header.touch()
        source.touch()
        parents = [("foo", int(NodeType.NAMESPACE)), ("Bar", int(NodeType.CLASS))]
        assert self._call(header, "baz", parents, tmp_path) == self._call(source, "baz", parents, tmp_path)

    def test_nested_namespace(self, tmp_path: Path) -> None:
        src = tmp_path / "a.cpp"
        src.touch()
        result = self._call(
            src,
            "run",
            [("outer", int(NodeType.NAMESPACE)), ("inner", int(NodeType.NAMESPACE)), ("Svc", int(NodeType.CLASS))],
            tmp_path,
        )
        assert result == "outer.inner.Svc.run"

    def test_template_class_parent_stripped(self, tmp_path: Path) -> None:
        src = tmp_path / "a.cpp"
        src.touch()
        result = self._call(
            src,
            "get",
            [("std", int(NodeType.NAMESPACE)), ("vector<int>", int(NodeType.CLASS))],
            tmp_path,
        )
        assert result == "std.vector.get"

    def test_free_function_uses_bare_symbol(self, tmp_path: Path) -> None:
        """A free function (no parent chain) uses just the bare symbol.

        File-path prefixing would make the same function's declaration
        (.hpp) and definition (.cpp) disagree, breaking cross-file
        references. Keeping the bare name also guarantees dual-registration
        aliases stay strictly shorter than the canonical scoped names, so
        the graph's longest-wins dedup picks the correct canonical entry.
        """
        src = tmp_path / "src" / "util.cpp"
        src.parent.mkdir()
        src.touch()
        result = self._call(src, "helper", [], tmp_path)
        assert result == "helper"

    def test_bare_name_is_shorter_than_scoped(self, tmp_path: Path) -> None:
        """Dual-registration aliases (parent_chain=[]) must be strictly
        shorter than the canonical scoped name so CallGraph.add_node's
        longest-wins dedup keeps the scoped name.
        """
        src = tmp_path / "a.cpp"
        src.touch()
        scoped = self._call(
            src,
            "Processor::process",
            [("services", int(NodeType.NAMESPACE))],
            tmp_path,
        )
        alias = self._call(src, "Processor::process", [], tmp_path)
        assert len(scoped) > len(alias), f"scoped {scoped!r} must be longer than alias {alias!r}"

    def test_inline_scope_in_parent_name_flattened(self, tmp_path: Path) -> None:
        """clangd sometimes emits ``foo::Bar`` as a single parent; must flatten."""
        src = tmp_path / "a.cpp"
        src.touch()
        result = self._call(src, "baz", [("foo::Bar", int(NodeType.CLASS))], tmp_path)
        assert result == "foo.Bar.baz"

    def test_macro_namespace_parent_dropped(self, tmp_path: Path) -> None:
        """Namespace-wrapper macros (``FMT_BEGIN_NAMESPACE`` etc.) surface as
        SymbolKind=STRING (15) parents from clangd. They must be dropped so
        the qualified name reflects the real namespace chain, not the macro
        identifier.
        """
        src = tmp_path / "a.cpp"
        src.touch()
        result = self._call(
            src,
            "format",
            [
                ("FMT_BEGIN_NAMESPACE", int(NodeType.STRING)),
                ("fmt", int(NodeType.NAMESPACE)),
                ("v11", int(NodeType.NAMESPACE)),
            ],
            tmp_path,
        )
        assert result == "fmt.v11.format"
