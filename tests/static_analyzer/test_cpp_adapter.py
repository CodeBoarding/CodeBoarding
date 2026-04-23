"""Tests for the C++ language adapter."""

from pathlib import Path

import pytest

from static_analyzer.constants import NodeType
from static_analyzer.engine.adapters import get_adapter
from static_analyzer.engine.adapters.cpp_adapter import (
    CppAdapter,
    _extract_signature,
    _normalize_cpp_parent,
    _strip_template_args,
)
from static_analyzer.engine.adapters.cpp_cdb import locate_generated_cdb, locate_user_cdb


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

    def test_accepts_generated_cdb_under_codeboarding_dir(self, tmp_path: Path) -> None:
        """A CDB written to .codeboarding/cdb/ (typically by a generator)
        must be detected so subsequent runs don't regenerate unnecessarily.
        """
        cdb_dir = tmp_path / ".codeboarding" / "cdb"
        cdb_dir.mkdir(parents=True)
        (cdb_dir / "compile_commands.json").write_text('[{"directory": ".", "file": "x.cc", "command": "c++ -c x.cc"}]')
        cmd = CppAdapter().get_lsp_command(tmp_path)
        assert any("--compile-commands-dir" in part for part in cmd), (
            "clangd's walk-up search would miss the hidden .codeboarding/cdb/ sibling; "
            "the adapter must pass --compile-commands-dir explicitly."
        )

    def test_rejects_empty_generated_cdb(self, tmp_path: Path) -> None:
        cdb_dir = tmp_path / ".codeboarding" / "cdb"
        cdb_dir.mkdir(parents=True)
        (cdb_dir / "compile_commands.json").write_text("[]")
        with pytest.raises(RuntimeError, match=r"compile_commands\.json"):
            CppAdapter().get_lsp_command(tmp_path)

    def test_no_compile_commands_dir_when_cdb_at_root(self, tmp_path: Path) -> None:
        """When the CDB lives at the project root (or build/) clangd finds
        it on its own — don't clutter the command line with a redundant flag.
        """
        (tmp_path / "compile_commands.json").write_text("[]")
        cmd = CppAdapter().get_lsp_command(tmp_path)
        assert not any("--compile-commands-dir" in part for part in cmd)

    def test_error_message_names_detected_build_system(self, tmp_path: Path) -> None:
        """CMake users get a CMake-specific hint, not the generic one."""
        (tmp_path / "CMakeLists.txt").write_text("project(x)")
        with pytest.raises(RuntimeError, match=r"CMAKE_EXPORT_COMPILE_COMMANDS"):
            CppAdapter().get_lsp_command(tmp_path)

    def test_error_message_names_bazel_when_detected(self, tmp_path: Path) -> None:
        (tmp_path / "MODULE.bazel").write_text("module(name='x')")
        with pytest.raises(RuntimeError, match=r"CODEBOARDING_CPP_GENERATE_CDB"):
            CppAdapter().get_lsp_command(tmp_path)


class TestCdbLocationSplit:
    """User-owned vs. generated CDB locations must be distinguishable so
    ``prepare_project`` can skip generation for the first but always defer
    to the generator's fingerprint cache for the second.
    """

    def test_find_user_cdb_at_root(self, tmp_path: Path) -> None:
        (tmp_path / "compile_commands.json").write_text("[]")
        assert locate_user_cdb(tmp_path) == tmp_path

    def test_find_user_cdb_in_build(self, tmp_path: Path) -> None:
        (tmp_path / "build").mkdir()
        (tmp_path / "build" / "compile_commands.json").write_text("[]")
        assert locate_user_cdb(tmp_path) == tmp_path / "build"

    def test_user_cdb_does_not_match_generated_dir(self, tmp_path: Path) -> None:
        """A CDB under ``.codeboarding/cdb/`` is *not* user-owned — the
        generator wrote it, and we're allowed to rebuild it.
        """
        cdb_dir = tmp_path / ".codeboarding" / "cdb"
        cdb_dir.mkdir(parents=True)
        (cdb_dir / "compile_commands.json").write_text('[{"directory": ".", "file": "x.cc", "command": "c++ -c x.cc"}]')
        assert locate_user_cdb(tmp_path) is None

    def test_find_generated_cdb_requires_validity(self, tmp_path: Path) -> None:
        cdb_dir = tmp_path / ".codeboarding" / "cdb"
        cdb_dir.mkdir(parents=True)
        (cdb_dir / "compile_commands.json").write_text("[]")  # invalid: empty
        assert locate_generated_cdb(tmp_path) is None


class TestExtractSignature:
    """Signature extraction for overload disambiguation.

    Must produce ``None`` for callables without params so that bare
    ``greet()`` methods don't all gain a needless ``()`` suffix — the
    fixture churn would be enormous for a gain that only materialises
    when overloads actually exist.
    """

    def test_empty_detail_returns_none(self) -> None:
        assert _extract_signature("") is None

    def test_no_paren_returns_none(self) -> None:
        assert _extract_signature("int") is None

    def test_empty_params_returns_none(self) -> None:
        """``() const -> void`` must produce None — no params, no overload risk."""
        assert _extract_signature("() const -> void") is None

    def test_single_param(self) -> None:
        assert _extract_signature("(int) -> void") == "(int)"

    def test_multiple_params(self) -> None:
        assert _extract_signature("(int, const std::string &) -> bool") == "(int, const std.string&)"

    def test_strips_whitespace_before_ref_and_pointer(self) -> None:
        """``const Entity &`` → ``const Entity&`` — canonical C++ reference form."""
        assert _extract_signature("(const Entity &) const") == "(const Entity&)"

    def test_collapses_double_spaces(self) -> None:
        assert _extract_signature("(int   x,   int  y)") == "(int x, int y)"

    def test_nested_template_args(self) -> None:
        """The outer ``(...)`` must balance despite ``<``/``>`` inside; we
        don't strip template args from the signature body — that's the
        adapter's job on the symbol name, not the detail.
        """
        assert _extract_signature("(std::vector<int> &)") == "(std.vector<int>&)"

    def test_unbalanced_returns_none(self) -> None:
        assert _extract_signature("(int, missing_close") is None


class TestBuildQualifiedNameOverloads:
    """build_qualified_name should gain a signature suffix for *callables*
    with a non-empty param list, and only for those.
    """

    def test_callable_with_params_gets_suffix(self, tmp_path: Path) -> None:
        adapter = CppAdapter()
        qname = adapter.build_qualified_name(
            file_path=tmp_path / "src" / "processor.cpp",
            symbol_name="process",
            symbol_kind=NodeType.METHOD,
            parent_chain=[("services", NodeType.NAMESPACE), ("Processor", NodeType.CLASS)],
            project_root=tmp_path,
            detail="(const Entity &) -> void",
        )
        assert qname == "services.Processor.process(const Entity&)"

    def test_callable_without_params_has_no_suffix(self, tmp_path: Path) -> None:
        adapter = CppAdapter()
        qname = adapter.build_qualified_name(
            file_path=tmp_path / "src" / "greeter.cpp",
            symbol_name="greet",
            symbol_kind=NodeType.METHOD,
            parent_chain=[("Greeter", NodeType.CLASS)],
            project_root=tmp_path,
            detail="() const -> void",
        )
        assert qname == "Greeter.greet"

    def test_non_callable_never_gets_suffix(self, tmp_path: Path) -> None:
        """A field / variable with a ``(`` accidentally in its detail (e.g.
        a function-pointer type) must not be treated as an overload.
        """
        adapter = CppAdapter()
        qname = adapter.build_qualified_name(
            file_path=tmp_path / "src" / "x.cpp",
            symbol_name="callback",
            symbol_kind=NodeType.FIELD,
            parent_chain=[("Foo", NodeType.CLASS)],
            project_root=tmp_path,
            detail="void(*)(int)",
        )
        assert qname == "Foo.callback"

    def test_constructor_overloads_distinguish(self, tmp_path: Path) -> None:
        """Two constructors with different signatures must produce different keys."""
        adapter = CppAdapter()
        default_ctor = adapter.build_qualified_name(
            file_path=tmp_path / "src" / "e.cpp",
            symbol_name="Entity",
            symbol_kind=NodeType.CONSTRUCTOR,
            parent_chain=[("models", NodeType.NAMESPACE), ("Entity", NodeType.CLASS)],
            project_root=tmp_path,
            detail="() -> void",
        )
        param_ctor = adapter.build_qualified_name(
            file_path=tmp_path / "src" / "e.cpp",
            symbol_name="Entity",
            symbol_kind=NodeType.CONSTRUCTOR,
            parent_chain=[("models", NodeType.NAMESPACE), ("Entity", NodeType.CLASS)],
            project_root=tmp_path,
            detail="(const std::string &) -> void",
        )
        assert default_ctor == "models.Entity.Entity"
        assert param_ctor == "models.Entity.Entity(const std.string&)"
        assert default_ctor != param_ctor


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

    def test_preserves_operator_parens(self):
        assert _strip_template_args("operator()") == "operator()"

    def test_preserves_operator_brackets(self):
        assert _strip_template_args("operator[]") == "operator[]"

    def test_preserves_operator_arrow(self):
        assert _strip_template_args("operator->") == "operator->"

    def test_preserves_operator_arrow_star(self):
        assert _strip_template_args("operator->*") == "operator->*"

    def test_preserves_operator_delete_array(self):
        assert _strip_template_args("operator delete[]") == "operator delete[]"

    def test_preserves_operator_comma(self):
        assert _strip_template_args("operator,") == "operator,"

    def test_preserves_conversion_operator_int(self):
        assert _strip_template_args("operator int") == "operator int"

    def test_preserves_conversion_operator_bool(self):
        """Conversion operators return a type, not a symbolic operator."""
        assert _strip_template_args("operator bool") == "operator bool"

    def test_cooperator_is_not_operator(self):
        """``cooperator<T>`` must strip templates — ``cooperator`` contains
        ``operator`` as a substring but is NOT an operator token.
        """
        assert _strip_template_args("cooperator<int>") == "cooperator"

    def test_empty_string_passes_through(self):
        assert _strip_template_args("") == ""

    def test_no_template_passes_through(self):
        assert _strip_template_args("plain_name") == "plain_name"

    def test_deeply_nested_templates(self):
        assert _strip_template_args("Foo<A<B<C<D>>>>") == "Foo"

    def test_adjacent_closing_angles(self):
        """C++11 ``A<B<C>>`` has two adjacent ``>`` — not a ``>>`` shift operator."""
        assert _strip_template_args("A<B<C>>") == "A"

    def test_unbalanced_open_only_passes_through(self):
        assert _strip_template_args("foo<bar") == "foo<bar"

    def test_unbalanced_extra_close_passes_through(self):
        assert _strip_template_args("foo>") == "foo>"

    def test_operator_at_start_of_name(self):
        """``operator+`` at position 0 — ``i == 0`` branch."""
        assert _strip_template_args("operator+") == "operator+"

    def test_operator_after_scope(self):
        """``std::operator==`` — operator after ``::`` delimiter."""
        assert _strip_template_args("std::operator==") == "std::operator=="

    def test_operator_call_with_template_parent(self):
        """``Foo<T>::operator()`` strips template args from parent, keeps operator."""
        assert _strip_template_args("Foo<T>::operator()") == "Foo::operator()"


class TestNormalizeCppParent:
    """Parent-chain normalization for ``build_qualified_name``."""

    def test_flattens_scope_operator(self):
        """``::`` becomes ``.`` (the project's universal delimiter)."""
        assert _normalize_cpp_parent("foo::bar") == "foo.bar"

    def test_drops_template_args(self):
        assert _normalize_cpp_parent("Foo<T>") == "Foo"

    def test_handles_template_and_scope_together(self):
        assert _normalize_cpp_parent("std::vector<int>") == "std.vector"

    def test_empty_string_passes_through(self):
        assert _normalize_cpp_parent("") == ""

    def test_whitespace_only_becomes_empty(self):
        assert _normalize_cpp_parent("   ") == ""

    def test_leading_trailing_whitespace_stripped(self):
        assert _normalize_cpp_parent("  foo::bar  ") == "foo.bar"

    def test_deeply_nested_scope_and_template(self):
        assert _normalize_cpp_parent("A::B<C>::D<E>::F") == "A.B.D.F"

    def test_anonymous_namespace(self):
        assert _normalize_cpp_parent("(anonymous namespace)::Foo") == "(anonymous namespace).Foo"

    def test_global_scope_prefix(self):
        assert _normalize_cpp_parent("::Foo::Bar") == ".Foo.Bar"


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

    def test_anonymous_namespace_parent(self, tmp_path: Path) -> None:
        """clangd emits ``(anonymous namespace)`` as a parent; must preserve it."""
        src = tmp_path / "a.cpp"
        src.touch()
        result = self._call(
            src,
            "helper",
            [("(anonymous namespace)", int(NodeType.NAMESPACE)), ("Svc", int(NodeType.CLASS))],
            tmp_path,
        )
        assert result == "(anonymous namespace).Svc.helper"

    def test_destructor_symbol(self, tmp_path: Path) -> None:
        """``~Entity`` in a namespace must keep the tilde."""
        src = tmp_path / "a.cpp"
        src.touch()
        result = self._call(
            src,
            "~Entity",
            [("models", int(NodeType.NAMESPACE)), ("Entity", int(NodeType.CLASS))],
            tmp_path,
        )
        assert result == "models.Entity.~Entity"

    def test_all_string_parents_drops_all(self, tmp_path: Path) -> None:
        """When every parent is STRING kind, all are dropped — bare name."""
        src = tmp_path / "a.cpp"
        src.touch()
        result = self._call(
            src,
            "format",
            [
                ("FMT_BEGIN_NAMESPACE", int(NodeType.STRING)),
                ("FMT_END_NAMESPACE", int(NodeType.STRING)),
            ],
            tmp_path,
        )
        assert result == "format"

    def test_symbol_with_scope_in_name_no_parents(self, tmp_path: Path) -> None:
        """A free-function symbol containing ``::`` is flattened to ``.``."""
        src = tmp_path / "a.cpp"
        src.touch()
        result = self._call(src, "std::move", [], tmp_path)
        assert result == "std.move"

    def test_template_symbol_no_parents(self, tmp_path: Path) -> None:
        """A template free function like ``swap<T>`` has args stripped."""
        src = tmp_path / "a.cpp"
        src.touch()
        result = self._call(src, "swap<T>", [], tmp_path)
        assert result == "swap"

    def test_enum_parent(self, tmp_path: Path) -> None:
        """Enums appear as parents for enum members."""
        src = tmp_path / "a.cpp"
        src.touch()
        result = self._call(
            src,
            "Red",
            [("colors", int(NodeType.NAMESPACE)), ("Color", int(NodeType.ENUM))],
            tmp_path,
        )
        assert result == "colors.Color.Red"

    def test_struct_parent(self, tmp_path: Path) -> None:
        src = tmp_path / "a.cpp"
        src.touch()
        result = self._call(
            src,
            "x",
            [("math", int(NodeType.NAMESPACE)), ("Vec2", int(NodeType.STRUCT))],
            tmp_path,
        )
        assert result == "math.Vec2.x"

    def test_build_release_subdir(self, tmp_path: Path) -> None:
        (tmp_path / "build" / "Release").mkdir(parents=True)
        (tmp_path / "build" / "Release" / "compile_commands.json").write_text("[]")
        cmd = CppAdapter().get_lsp_command(tmp_path)
        assert cmd

    def test_cmake_build_release(self, tmp_path: Path) -> None:
        (tmp_path / "cmake-build-release").mkdir()
        (tmp_path / "cmake-build-release" / "compile_commands.json").write_text("[]")
        cmd = CppAdapter().get_lsp_command(tmp_path)
        assert cmd

    def test_both_compile_db_and_flags(self, tmp_path: Path) -> None:
        """When both exist, either one is sufficient."""
        (tmp_path / "compile_commands.json").write_text("[]")
        (tmp_path / "compile_flags.txt").write_text("-std=c++20\n")
        cmd = CppAdapter().get_lsp_command(tmp_path)
        assert cmd

    def test_compile_flags_in_build_debug(self, tmp_path: Path) -> None:
        (tmp_path / "build" / "Debug").mkdir(parents=True)
        (tmp_path / "build" / "Debug" / "compile_flags.txt").write_text("-std=c++17\n")
        cmd = CppAdapter().get_lsp_command(tmp_path)
        assert cmd

    def test_empty_parent_chain_entry_skipped(self, tmp_path: Path) -> None:
        """A parent that normalizes to empty (e.g. whitespace-only) is dropped."""
        src = tmp_path / "a.cpp"
        src.touch()
        result = self._call(
            src,
            "method",
            [("  ", int(NodeType.NAMESPACE)), ("Foo", int(NodeType.CLASS))],
            tmp_path,
        )
        assert result == "Foo.method"
