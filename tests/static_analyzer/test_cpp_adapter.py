"""Tests for the C++ language adapter."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from static_analyzer.constants import NodeType
from static_analyzer.engine.adapters import get_adapter
from static_analyzer.engine.adapters.cpp_adapter import (
    CppAdapter,
    _extract_signature,
    _normalize_cpp_parent,
    _strip_template_args,
)
from static_analyzer.cdb import locate_generated_cdb, locate_user_cdb
from static_analyzer.cdb.base import BuildSystemKind

_VALID_CDB = '[{"directory": ".", "file": "x.cc", "command": "c++ -c x.cc"}]'


class TestCppAdapterProperties:
    @pytest.mark.parametrize(
        "attr, expected",
        [
            ("language", "Cpp"),
            ("language_id", "cpp"),
            ("lsp_command", ["clangd"]),
        ],
    )
    def test_trivial_getters(self, attr: str, expected: object) -> None:
        assert getattr(CppAdapter(), attr) == expected

    def test_file_extensions_cover_cpp_c_and_headers(self):
        exts = set(CppAdapter().file_extensions)
        assert {".cpp", ".cc", ".cxx"}.issubset(exts)
        assert {".hpp", ".hh", ".h"}.issubset(exts)
        assert ".c" in exts, "C sources must be indexed — clangd handles both dialects"

    def test_registry_returns_cpp_adapter(self):
        adapter = get_adapter("Cpp")
        assert isinstance(adapter, CppAdapter)

    def test_references_per_query_timeout_nonzero(self):
        assert CppAdapter().references_per_query_timeout > 0

    def test_phase1_request_timeout_uses_probe_timeout(self):
        probe = 1670
        assert CppAdapter().phase1_request_timeout(probe) == probe

    def test_namespaces_are_reference_worthy(self):
        assert CppAdapter().is_reference_worthy(NodeType.NAMESPACE) is True

    def test_cpp_adapter_fallback_flags_dialect_neutral(self) -> None:
        """HIGH#2: post-collapse CppAdapter indexes ``.c`` files too, so it
        must NOT pin ``-std=c++20`` in fallbackFlags — clangd's dialect-aware
        defaults (driven by ``language_id_for_file``) cover both dialects.
        """
        opts = CppAdapter().get_lsp_init_options()
        flags = opts.get("fallbackFlags", [])
        assert "-std=c++20" not in flags
        assert not any(flag.startswith("-std=c++") for flag in flags)

    def test_mixed_project_c_files_do_not_get_cpp20_fallback(self) -> None:
        """HIGH#2 regression: in a mixed project the only surviving adapter
        is ``CppAdapter`` (CAdapter gets collapsed), so its fallback flags
        apply to ``.c`` files not covered by the CDB. ``-std=c++20`` here
        would force a C++ dialect on plain C sources.
        """
        flags = CppAdapter().get_lsp_init_options().get("fallbackFlags", [])
        assert "-std=c++20" not in flags


class TestCppLanguageIdForFile:
    """``language_id_for_file`` reports each file's true dialect so clangd
    doesn't parse ``.c`` sources as C++ after the mixed-project collapse.
    """

    @pytest.mark.parametrize("ext", [".c"])
    def test_language_id_for_file_returns_c_for_c_extensions(self, ext: str) -> None:
        assert CppAdapter().language_id_for_file(Path(f"foo{ext}")) == "c"

    def test_language_id_for_file_returns_cpp_for_h_in_cpp_adapter(self) -> None:
        # Why: ``.h`` is shared between C and C++ but is overwhelmingly C++
        # in mixed repos (POCO, LLVM, Boost, ...). Announcing it as ``"c"``
        # drops every namespace/template/class symbol in those headers.
        # Pure-C projects route through ``CAdapter`` whose override pins
        # ``.h`` back to ``"c"``.
        assert CppAdapter().language_id_for_file(Path("api.h")) == "cpp"

    @pytest.mark.parametrize("ext", [".cpp", ".cc", ".cxx", ".hpp", ".hh", ".h"])
    def test_language_id_for_file_returns_cpp_for_cpp_extensions(self, ext: str) -> None:
        assert CppAdapter().language_id_for_file(Path(f"foo{ext}")) == "cpp"


class TestCompilationDatabaseGuard:
    def test_raises_when_no_compilation_database(self, tmp_path: Path) -> None:
        with pytest.raises(RuntimeError, match=r"compile_commands\.json"):
            CppAdapter().get_lsp_command(tmp_path)

    def test_accepts_compile_flags_txt_at_root(self, tmp_path: Path) -> None:
        (tmp_path / "compile_flags.txt").write_text("-std=c++17\n")
        cmd = CppAdapter().get_lsp_command(tmp_path)
        assert cmd
        assert any("clangd" in part for part in cmd)

    def test_accepts_compile_commands_at_root(self, tmp_path: Path) -> None:
        (tmp_path / "compile_commands.json").write_text(_VALID_CDB)
        cmd = CppAdapter().get_lsp_command(tmp_path)
        assert cmd
        assert any("clangd" in part for part in cmd)

    def test_accepts_compile_commands_in_build_subdir(self, tmp_path: Path) -> None:
        (tmp_path / "build").mkdir()
        (tmp_path / "build" / "compile_commands.json").write_text(_VALID_CDB)
        cmd = CppAdapter().get_lsp_command(tmp_path)
        assert cmd

    def test_accepts_cmake_build_debug(self, tmp_path: Path) -> None:
        (tmp_path / "cmake-build-debug").mkdir()
        (tmp_path / "cmake-build-debug" / "compile_commands.json").write_text(_VALID_CDB)
        cmd = CppAdapter().get_lsp_command(tmp_path)
        assert cmd

    def test_rejects_empty_user_compile_commands(self, tmp_path: Path) -> None:
        """M2: empty array at the user path must not be accepted as a CDB."""
        (tmp_path / "compile_commands.json").write_text("[]")
        with pytest.raises(RuntimeError, match=r"compile_commands\.json"):
            CppAdapter().get_lsp_command(tmp_path)

    def test_accepts_generated_cdb_under_codeboarding_dir(self, tmp_path: Path) -> None:
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
        (tmp_path / "compile_commands.json").write_text(_VALID_CDB)
        cmd = CppAdapter().get_lsp_command(tmp_path)
        assert not any("--compile-commands-dir" in part for part in cmd)

    def test_error_message_names_detected_build_system(self, tmp_path: Path) -> None:
        (tmp_path / "CMakeLists.txt").write_text("project(x)")
        with pytest.raises(RuntimeError, match=r"CMAKE_EXPORT_COMPILE_COMMANDS"):
            CppAdapter().get_lsp_command(tmp_path)

    def test_error_message_names_bazel_when_detected(self, tmp_path: Path) -> None:
        (tmp_path / "MODULE.bazel").write_text("module(name='x')")
        with pytest.raises(RuntimeError, match=r"CODEBOARDING_CPP_GENERATE_CDB"):
            CppAdapter().get_lsp_command(tmp_path)


class TestCdbLocationSplit:
    def test_find_user_cdb_at_root(self, tmp_path: Path) -> None:
        (tmp_path / "compile_commands.json").write_text(_VALID_CDB)
        assert locate_user_cdb(tmp_path) == tmp_path

    def test_find_user_cdb_in_build(self, tmp_path: Path) -> None:
        (tmp_path / "build").mkdir()
        (tmp_path / "build" / "compile_commands.json").write_text(_VALID_CDB)
        assert locate_user_cdb(tmp_path) == tmp_path / "build"

    def test_user_cdb_does_not_match_generated_dir(self, tmp_path: Path) -> None:
        cdb_dir = tmp_path / ".codeboarding" / "cdb"
        cdb_dir.mkdir(parents=True)
        (cdb_dir / "compile_commands.json").write_text(_VALID_CDB)
        assert locate_user_cdb(tmp_path) is None

    def test_find_generated_cdb_requires_validity(self, tmp_path: Path) -> None:
        cdb_dir = tmp_path / ".codeboarding" / "cdb"
        cdb_dir.mkdir(parents=True)
        (cdb_dir / "compile_commands.json").write_text("[]")  # invalid: empty
        assert locate_generated_cdb(tmp_path) is None


class TestExtractSignature:
    def test_empty_detail_returns_none(self) -> None:
        assert _extract_signature("") is None

    def test_no_paren_returns_none(self) -> None:
        assert _extract_signature("int") is None

    def test_empty_params_returns_none(self) -> None:
        assert _extract_signature("() const -> void") is None

    def test_single_param(self) -> None:
        assert _extract_signature("(int) -> void") == "(int)"

    def test_multiple_params(self) -> None:
        assert _extract_signature("(int, const std::string &) -> bool") == "(int, const string&)"

    def test_strips_whitespace_before_ref_and_pointer(self) -> None:
        assert _extract_signature("(const Entity &) const") == "(const Entity&)"

    def test_collapses_double_spaces(self) -> None:
        assert _extract_signature("(int   x,   int  y)") == "(int x, int y)"

    def test_nested_template_args(self) -> None:
        """Outer ``(...)`` must balance despite inner ``<``/``>``; template args
        in the signature body are kept (adapter strips them on the symbol name,
        not on detail).
        """
        assert _extract_signature("(std::vector<int> &)") == "(vector<int>&)"

    def test_unbalanced_returns_none(self) -> None:
        assert _extract_signature("(int, missing_close") is None

    def test_extract_signature_strips_std_prefix(self) -> None:
        """M11: clangd 22.x drops ``std::`` from common std-lib tokens; pin
        the format so hashes stay stable across clangd versions.
        """
        assert _extract_signature("int Foo::bar(std::vector<int>&)") == "(vector<int>&)"

    def test_extract_signature_strips_nested_std_prefix(self) -> None:
        assert _extract_signature("void f(std::map<std::string, int>&)") == "(map<string, int>&)"

    def test_extract_signature_does_not_strip_non_std_prefix(self) -> None:
        """``mystd`` is a pseudo-namespace; the negative lookbehind preserves it."""
        assert _extract_signature("void f(mystd::vector<int>&)") == "(mystd.vector<int>&)"

    def test_extract_signature_strips_std_when_following_template_open(self) -> None:
        """``<std::...>`` boundary is a non-identifier char; strip applies."""
        assert _extract_signature("void f(unique_ptr<std::string>&)") == "(unique_ptr<string>&)"


class TestBuildQualifiedNameOverloads:
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
        """Field/variable with ``(`` in its detail (e.g. function-pointer type)
        must not be treated as an overload.
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
        assert param_ctor == "models.Entity.Entity(const string&)"
        assert default_ctor != param_ctor


class TestStripTemplateArgs:
    def test_drops_simple_template(self):
        assert _strip_template_args("vector<int>") == "vector"

    def test_drops_nested_templates(self):
        assert _strip_template_args("map<string, vector<int>>") == "map"

    def test_preserves_scope_qualifiers(self):
        assert _strip_template_args("std::vector<int>") == "std::vector"

    def test_preserves_operator_less_than(self):
        assert _strip_template_args("operator<") == "operator<"

    def test_preserves_operator_shift(self):
        assert _strip_template_args("operator<<") == "operator<<"

    def test_preserves_three_way_comparison(self):
        """C++20 ``operator<=>`` must not collapse to ``operator`` via the
        balanced-bracket rule.
        """
        assert _strip_template_args("operator<=>") == "operator<=>"

    def test_preserves_qualified_operator(self):
        assert _strip_template_args("Foo::operator<=>") == "Foo::operator<=>"

    def test_preserves_operator_with_template_class_parent(self):
        assert _strip_template_args("Foo<T>::operator<=>") == "Foo::operator<=>"

    def test_preserves_operator_equals(self):
        assert _strip_template_args("operator==") == "operator=="

    def test_preserves_operator_new(self):
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
        assert _strip_template_args("operator bool") == "operator bool"

    def test_cooperator_is_not_operator(self):
        """``cooperator`` contains ``operator`` as a substring but is not an
        operator token — templates must still strip.
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
        assert _strip_template_args("operator+") == "operator+"

    def test_operator_after_scope(self):
        assert _strip_template_args("std::operator==") == "std::operator=="

    def test_operator_call_with_template_parent(self):
        assert _strip_template_args("Foo<T>::operator()") == "Foo::operator()"


class TestNormalizeCppParent:
    def test_flattens_scope_operator(self):
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
        src = tmp_path / "src" / "foo.cpp"
        src.parent.mkdir(parents=True)
        src.touch()
        result = self._call(src, "baz", [("foo", int(NodeType.NAMESPACE)), ("Bar", int(NodeType.CLASS))], tmp_path)
        assert result == "foo.Bar.baz"

    def test_same_name_in_header_and_source(self, tmp_path: Path) -> None:
        """Declaration (.hpp) and definition (.cpp) must produce identical names."""
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

    def test_free_function_prefixes_file_stem(self, tmp_path: Path) -> None:
        """Free function (no parent chain) gets file-stem prefix (M11).

        Why: two unrelated global ``helper`` functions in different TUs would
        otherwise overwrite each other in the SymbolTable. Headers and matching
        sources share a stem (``util.hpp`` / ``util.cpp``), preserving the
        cross-file reference within a single translation unit.
        """
        src = tmp_path / "src" / "util.cpp"
        src.parent.mkdir()
        src.touch()
        result = self._call(src, "helper", [], tmp_path)
        assert result == "util.helper"

    def test_bare_name_is_shorter_than_scoped(self, tmp_path: Path) -> None:
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
        src = tmp_path / "a.cpp"
        src.touch()
        result = self._call(src, "baz", [("foo::Bar", int(NodeType.CLASS))], tmp_path)
        assert result == "foo.Bar.baz"

    def test_macro_namespace_parent_dropped(self, tmp_path: Path) -> None:
        """Namespace-wrapper macros (``FMT_BEGIN_NAMESPACE`` etc.) surface as
        SymbolKind=STRING parents from clangd; must be dropped so the qualified
        name reflects the real namespace chain.
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
        """All-STRING parent chain collapses to the no-parent case -> file-stem prefix."""
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
        assert result == "a.format"

    def test_symbol_with_scope_in_name_no_parents(self, tmp_path: Path) -> None:
        src = tmp_path / "a.cpp"
        src.touch()
        result = self._call(src, "std::move", [], tmp_path)
        assert result == "a.std.move"

    def test_template_symbol_no_parents(self, tmp_path: Path) -> None:
        src = tmp_path / "a.cpp"
        src.touch()
        result = self._call(src, "swap<T>", [], tmp_path)
        assert result == "a.swap"

    def test_enum_parent(self, tmp_path: Path) -> None:
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
        (tmp_path / "build" / "Release" / "compile_commands.json").write_text(_VALID_CDB)
        cmd = CppAdapter().get_lsp_command(tmp_path)
        assert cmd

    def test_cmake_build_release(self, tmp_path: Path) -> None:
        (tmp_path / "cmake-build-release").mkdir()
        (tmp_path / "cmake-build-release" / "compile_commands.json").write_text(_VALID_CDB)
        cmd = CppAdapter().get_lsp_command(tmp_path)
        assert cmd

    def test_both_compile_db_and_flags(self, tmp_path: Path) -> None:
        (tmp_path / "compile_commands.json").write_text(_VALID_CDB)
        (tmp_path / "compile_flags.txt").write_text("-std=c++20\n")
        cmd = CppAdapter().get_lsp_command(tmp_path)
        assert cmd

    def test_compile_flags_in_build_debug(self, tmp_path: Path) -> None:
        (tmp_path / "build" / "Debug").mkdir(parents=True)
        (tmp_path / "build" / "Debug" / "compile_flags.txt").write_text("-std=c++17\n")
        cmd = CppAdapter().get_lsp_command(tmp_path)
        assert cmd

    def test_empty_parent_chain_entry_skipped(self, tmp_path: Path) -> None:
        src = tmp_path / "a.cpp"
        src.touch()
        result = self._call(
            src,
            "method",
            [("  ", int(NodeType.NAMESPACE)), ("Foo", int(NodeType.CLASS))],
            tmp_path,
        )
        assert result == "Foo.method"

    def test_build_qualified_name_no_parent_prefixes_file_stem(self, tmp_path: Path) -> None:
        """M11: no-parent globals must include the file stem so two ``add``
        helpers in different TUs don't overwrite each other in SymbolTable.
        """
        src = tmp_path / "src" / "foo.cpp"
        src.parent.mkdir(parents=True)
        src.touch()
        assert self._call(src, "add", [], tmp_path) == "foo.add"

    def test_build_qualified_name_with_parent_does_not_prefix_file_stem(self, tmp_path: Path) -> None:
        """Parent-chain case stays backward-compatible: no file-stem prefix."""
        src = tmp_path / "src" / "foo.cpp"
        src.parent.mkdir(parents=True)
        src.touch()
        assert self._call(src, "method", [("Bar", int(NodeType.CLASS))], tmp_path) == "Bar.method"

    def test_build_qualified_name_string_parent_chain_treated_as_no_parent(self, tmp_path: Path) -> None:
        """STRING-only parents are dropped -> behaves like no-parent -> file-stem prefix."""
        src = tmp_path / "src" / "foo.cpp"
        src.parent.mkdir(parents=True)
        src.touch()
        result = self._call(src, "format", [("FMT_BEGIN_NAMESPACE", int(NodeType.STRING))], tmp_path)
        assert result == "foo.format"

    def test_build_qualified_name_no_parent_globals_in_different_files_dont_collide(self, tmp_path: Path) -> None:
        """Two ``add`` symbols in foo.cpp and bar.cpp must get distinct names (M11)."""
        (tmp_path / "src").mkdir()
        foo = tmp_path / "src" / "foo.cpp"
        bar = tmp_path / "src" / "bar.cpp"
        foo.touch()
        bar.touch()
        foo_add = self._call(foo, "add", [], tmp_path)
        bar_add = self._call(bar, "add", [], tmp_path)
        assert foo_add == "foo.add"
        assert bar_add == "bar.add"
        assert foo_add != bar_add


class TestPrepareProjectSkipConditions:
    def test_skip_when_user_cdb_already_present(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """A user-owned compile_commands.json short-circuits even with opt-in set."""
        monkeypatch.setenv("CODEBOARDING_CPP_GENERATE_CDB", "1")
        (tmp_path / "compile_commands.json").write_text(_VALID_CDB)
        with patch("static_analyzer.cdb.generator_for") as gen_for:
            CppAdapter().prepare_project(tmp_path)
        gen_for.assert_not_called()

    def test_generated_cdb_does_not_short_circuit_generator(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A stale CDB under ``.codeboarding/cdb/`` must NOT skip the generator --
        the generator owns the fingerprint cache.
        """
        monkeypatch.setenv("CODEBOARDING_CPP_GENERATE_CDB", "1")
        (tmp_path / "Makefile").write_text("all:\n")
        cdb_dir = tmp_path / ".codeboarding" / "cdb"
        cdb_dir.mkdir(parents=True)
        (cdb_dir / "compile_commands.json").write_text('[{"directory": ".", "file": "x.cc", "command": "c++"}]')

        fake_generator = MagicMock()
        fake_generator.generate.return_value = cdb_dir / "compile_commands.json"
        fake_generator.kind = BuildSystemKind.MAKE
        with patch("static_analyzer.cdb.generator_for", return_value=fake_generator):
            CppAdapter().prepare_project(tmp_path)
        fake_generator.generate.assert_called_once_with(tmp_path, tmp_path)

    def test_skip_when_optin_not_set(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("CODEBOARDING_CPP_GENERATE_CDB", raising=False)
        (tmp_path / "Makefile").write_text("all:\n")
        with patch("static_analyzer.cdb.generator_for") as gen_for:
            CppAdapter().prepare_project(tmp_path)
        gen_for.assert_not_called()

    def test_skip_when_kind_has_no_generator(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("CODEBOARDING_CPP_GENERATE_CDB", "1")
        (tmp_path / "CMakeLists.txt").write_text("project(x)")
        CppAdapter().prepare_project(tmp_path)  # Must not raise
        assert not (tmp_path / ".codeboarding" / "cdb" / "compile_commands.json").is_file()


class TestPrepareProjectInvokesBearForMake:
    def test_make_project_calls_bear_generator(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("CODEBOARDING_CPP_GENERATE_CDB", "1")
        (tmp_path / "Makefile").write_text("all:\n")

        fake_generator = MagicMock()
        fake_generator.generate.return_value = tmp_path / ".codeboarding" / "cdb" / "compile_commands.json"
        fake_generator.kind = BuildSystemKind.MAKE
        with patch("static_analyzer.cdb.generator_for", return_value=fake_generator):
            CppAdapter().prepare_project(tmp_path)
        fake_generator.generate.assert_called_once_with(tmp_path, tmp_path)

    def test_subdir_build_root_routes_cdb_to_analysis_root(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Stockfish-shape (H2): Makefile in src/, but the CDB must land under
        the analysis root so ``get_lsp_command`` finds it."""
        monkeypatch.setenv("CODEBOARDING_CPP_GENERATE_CDB", "1")
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "Makefile").write_text("all:\n")

        fake_generator = MagicMock()
        fake_generator.generate.return_value = tmp_path / ".codeboarding" / "cdb" / "compile_commands.json"
        fake_generator.kind = BuildSystemKind.MAKE
        with patch("static_analyzer.cdb.generator_for", return_value=fake_generator):
            CppAdapter().prepare_project(tmp_path)
        # analysis_root = tmp_path, build_cwd = tmp_path/src
        fake_generator.generate.assert_called_once_with(tmp_path, tmp_path / "src")

    def test_generator_failure_is_swallowed(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        """A failing generator must log but not raise -- ``get_lsp_command`` owns
        the user-facing error surface.
        """
        monkeypatch.setenv("CODEBOARDING_CPP_GENERATE_CDB", "1")
        (tmp_path / "Makefile").write_text("all:\n")

        fake_generator = MagicMock()
        fake_generator.generate.side_effect = RuntimeError("make exploded")
        fake_generator.kind = BuildSystemKind.MAKE
        with patch("static_analyzer.cdb.generator_for", return_value=fake_generator):
            CppAdapter().prepare_project(tmp_path)  # Must NOT raise
        assert "CDB generation failed" in caplog.text


class TestPrepareProjectEnvOverride:
    """``CODEBOARDING_CPP_BUILD_SYSTEM`` override semantics (M7)."""

    def test_rejects_non_buildable_compile_flags_txt(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        """``compile_flags_txt`` is no longer a member of ``BuildSystemKind``
        -- the override must warn and fall back to detection."""
        monkeypatch.setenv("CODEBOARDING_CPP_GENERATE_CDB", "1")
        monkeypatch.setenv("CODEBOARDING_CPP_BUILD_SYSTEM", "compile_flags_txt")
        (tmp_path / "Makefile").write_text("all:\n")

        fake_generator = MagicMock()
        fake_generator.generate.return_value = tmp_path / ".codeboarding" / "cdb" / "compile_commands.json"
        fake_generator.kind = BuildSystemKind.MAKE
        with patch("static_analyzer.cdb.generator_for", return_value=fake_generator):
            CppAdapter().prepare_project(tmp_path)

        assert "compile_flags_txt" in caplog.text
        # Falls back to detected MAKE.
        fake_generator.generate.assert_called_once()

    def test_rejects_unknown_override(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        """``unknown`` is a sentinel, not a buildable kind."""
        monkeypatch.setenv("CODEBOARDING_CPP_GENERATE_CDB", "1")
        monkeypatch.setenv("CODEBOARDING_CPP_BUILD_SYSTEM", "unknown")
        (tmp_path / "Makefile").write_text("all:\n")

        fake_generator = MagicMock()
        fake_generator.kind = BuildSystemKind.MAKE
        with patch("static_analyzer.cdb.generator_for", return_value=fake_generator):
            CppAdapter().prepare_project(tmp_path)
        assert "not a buildable" in caplog.text or "unknown" in caplog.text

    def test_unknown_string_warns(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        monkeypatch.setenv("CODEBOARDING_CPP_GENERATE_CDB", "1")
        monkeypatch.setenv("CODEBOARDING_CPP_BUILD_SYSTEM", "rust_cargo")
        (tmp_path / "Makefile").write_text("all:\n")

        fake_generator = MagicMock()
        fake_generator.kind = BuildSystemKind.MAKE
        with patch("static_analyzer.cdb.generator_for", return_value=fake_generator):
            CppAdapter().prepare_project(tmp_path)
        assert "rust_cargo" in caplog.text or "unknown" in caplog.text.lower()


class TestUserCdbSubdirCompileCommandsDir:
    """HIGH#3: user CDB in a subdir must surface via ``--compile-commands-dir``.

    Without the flag clangd only finds the CDB when walking up from sources
    inside that subdir, so sibling-dir files get indexed with no flags.
    """

    def test_user_cdb_in_subdir_adds_compile_commands_dir_flag(self, tmp_path: Path) -> None:
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "compile_commands.json").write_text(_VALID_CDB)
        adapter = CppAdapter()
        adapter.prepare_project(tmp_path)
        cmd = adapter.get_lsp_command(tmp_path)
        flag = next((p for p in cmd if "--compile-commands-dir" in p), None)
        assert flag is not None, f"expected --compile-commands-dir in {cmd!r}"
        assert flag.endswith(str((tmp_path / "src").resolve())), flag

    def test_user_cdb_at_root_does_not_add_compile_commands_dir_flag(self, tmp_path: Path) -> None:
        (tmp_path / "compile_commands.json").write_text(_VALID_CDB)
        adapter = CppAdapter()
        adapter.prepare_project(tmp_path)
        cmd = adapter.get_lsp_command(tmp_path)
        assert not any("--compile-commands-dir" in p for p in cmd)


class TestDottedFilenameQualifiedName:
    """HIGH#4: ``Path.stem`` strips only the final suffix, so dotted filenames
    (``simd.x86.cpp``) need normalisation or globals collide with real scopes.
    """

    def test_build_qualified_name_dotted_filename_does_not_collide(self, tmp_path: Path) -> None:
        adapter = CppAdapter()
        dotted = tmp_path / "simd.x86.cpp"
        dotted.touch()
        qname = adapter.build_qualified_name(
            file_path=dotted,
            symbol_name="baz",
            symbol_kind=int(NodeType.FUNCTION),
            parent_chain=[],
            project_root=tmp_path,
        )
        # Must not be ``simd.x86.baz`` -- that collides with ``simd::x86::baz``.
        assert qname == "simd_x86.baz"

    def test_build_qualified_name_filename_with_multiple_dots_normalized(self, tmp_path: Path) -> None:
        adapter = CppAdapter()
        src = tmp_path / "a.b.c.cpp"
        src.touch()
        qname = adapter.build_qualified_name(
            file_path=src,
            symbol_name="f",
            symbol_kind=int(NodeType.FUNCTION),
            parent_chain=[],
            project_root=tmp_path,
        )
        assert qname == "a_b_c.f"


class TestExtractSignatureOperatorOverloads:
    """HIGH#5: ``operator()`` / ``operator[]`` overloads must not have their own
    parens / brackets mistaken for the parameter list.
    """

    def test_extract_signature_handles_operator_call(self) -> None:
        assert _extract_signature("void operator()(int x)") == "(int x)"

    def test_extract_signature_handles_operator_call_const(self) -> None:
        assert _extract_signature("int operator()(double x) const") == "(double x)"

    def test_extract_signature_handles_operator_subscript(self) -> None:
        assert _extract_signature("int& operator[](size_t i)") == "(size_t i)"

    def test_extract_signature_function_call_operator_overloads_distinguish(self, tmp_path: Path) -> None:
        """Two ``operator()`` overloads on the same functor must get distinct
        qualified names — otherwise they overwrite each other in SymbolTable.
        """
        adapter = CppAdapter()
        src = tmp_path / "fn.cpp"
        src.touch()
        parents = [("Functor", int(NodeType.CLASS))]
        int_overload = adapter.build_qualified_name(
            file_path=src,
            symbol_name="operator()",
            symbol_kind=int(NodeType.METHOD),
            parent_chain=parents,
            project_root=tmp_path,
            detail="void operator()(int)",
        )
        double_overload = adapter.build_qualified_name(
            file_path=src,
            symbol_name="operator()",
            symbol_kind=int(NodeType.METHOD),
            parent_chain=parents,
            project_root=tmp_path,
            detail="void operator()(double)",
        )
        assert int_overload != double_overload
        assert int_overload.endswith("(int)")
        assert double_overload.endswith("(double)")


class TestExtractSignatureConversionOperators:
    """HIGH#2: no-arg conversion operators (``operator double()``) must not
    have their own ``()`` mistaken for an ``operator()`` step-over. The body
    after the operator-type identifier IS the param list -- empty here, but
    we still emit ``"()"`` so two distinct conversion operators on one class
    don't collide downstream.
    """

    def test_extract_signature_no_arg_conversion_operator_double(self) -> None:
        # Pre-fix: step-over ate the param list -> body unbalanced -> None.
        sig = _extract_signature("operator double()")
        assert sig is not None
        assert sig == "()"

    def test_extract_signature_no_arg_conversion_operator_int(self) -> None:
        sig = _extract_signature("operator int()")
        assert sig is not None
        assert sig == "()"

    def test_extract_signature_no_arg_conversion_operator_bool(self) -> None:
        sig = _extract_signature("operator bool() const")
        assert sig is not None
        assert sig == "()"

    def test_extract_signature_conversion_operator_with_args(self) -> None:
        assert _extract_signature("operator int(double)") == "(double)"

    def test_extract_signature_conversion_operator_new_no_args(self) -> None:
        """``operator new`` has no ``()`` -> existing logic returns None
        (no step-over triggers because there's nothing past ``new``)."""
        assert _extract_signature("operator new") is None

    def test_extract_signature_punctuation_operator_with_args(self) -> None:
        """``operator+(int)`` -- punctuation form, no step-over needed."""
        assert _extract_signature("operator+(int)") == "(int)"

    def test_extract_signature_operator_call_then_call(self) -> None:
        """``operator()() const`` -- step over the operator's own ``()``,
        then the trailing ``()`` is the (empty) param list. Empty but
        ``after_operator=True`` -> emit ``"()"``."""
        assert _extract_signature("operator()() const") == "()"

    def test_distinct_conversion_operators_get_distinct_signatures(self, tmp_path: Path) -> None:
        """HIGH#2 functional: ``operator double()`` and ``operator int()`` on
        the same class must produce distinct qualified names via
        ``build_qualified_name``.
        """
        adapter = CppAdapter()
        src = tmp_path / "v.cpp"
        src.touch()
        parents = [("Variant", int(NodeType.CLASS))]
        as_double = adapter.build_qualified_name(
            file_path=src,
            symbol_name="operator double",
            symbol_kind=int(NodeType.METHOD),
            parent_chain=parents,
            project_root=tmp_path,
            detail="operator double() const",
        )
        as_int = adapter.build_qualified_name(
            file_path=src,
            symbol_name="operator int",
            symbol_kind=int(NodeType.METHOD),
            parent_chain=parents,
            project_root=tmp_path,
            detail="operator int() const",
        )
        assert as_double != as_int


class TestGetLspCommandTocTouRevalidation:
    """HIGH#3: ``prepare_project`` caches the CDB resolution; if the user
    runs ``make clean`` between then and ``get_lsp_command`` clangd silently
    starts pointing at a missing file and returns empty refs. Re-stat on
    every ``get_lsp_command`` call so we surface the same actionable error
    as the missing-CDB path.
    """

    def test_get_lsp_command_revalidates_user_cdb_at_root(self, tmp_path: Path) -> None:
        cdb = tmp_path / "compile_commands.json"
        cdb.write_text(_VALID_CDB)
        adapter = CppAdapter()
        adapter.prepare_project(tmp_path)
        cdb.unlink()  # ``make clean`` between prepare_project and now
        with pytest.raises(RuntimeError, match=r"compile_commands\.json"):
            adapter.get_lsp_command(tmp_path)

    def test_get_lsp_command_revalidates_user_cdb_in_subdir(self, tmp_path: Path) -> None:
        (tmp_path / "src").mkdir()
        cdb = tmp_path / "src" / "compile_commands.json"
        cdb.write_text(_VALID_CDB)
        adapter = CppAdapter()
        adapter.prepare_project(tmp_path)
        cdb.unlink()
        with pytest.raises(RuntimeError, match=r"compile_commands\.json"):
            adapter.get_lsp_command(tmp_path)

    def test_get_lsp_command_revalidates_generated_cdb(self, tmp_path: Path) -> None:
        cdb_dir = tmp_path / ".codeboarding" / "cdb"
        cdb_dir.mkdir(parents=True)
        cdb = cdb_dir / "compile_commands.json"
        cdb.write_text(_VALID_CDB)
        adapter = CppAdapter()
        adapter.prepare_project(tmp_path)
        cdb.unlink()
        with pytest.raises(RuntimeError, match=r"compile_commands\.json"):
            adapter.get_lsp_command(tmp_path)

    def test_get_lsp_command_succeeds_when_cdb_present(self, tmp_path: Path) -> None:
        """Control: no removal between prepare_project and get_lsp_command --
        must produce a runnable command (regression guard for the TOCTOU fix)."""
        (tmp_path / "compile_commands.json").write_text(_VALID_CDB)
        adapter = CppAdapter()
        adapter.prepare_project(tmp_path)
        cmd = adapter.get_lsp_command(tmp_path)
        assert cmd
        assert any("clangd" in part for part in cmd)

    def test_get_lsp_command_revalidates_compile_flags_txt_at_root(self, tmp_path: Path) -> None:
        """A ``compile_flags.txt`` deleted between calls must also surface
        the missing-CDB error -- the at-root path accepts either file."""
        flags = tmp_path / "compile_flags.txt"
        flags.write_text("-std=c++17\n")
        adapter = CppAdapter()
        adapter.prepare_project(tmp_path)
        flags.unlink()
        with pytest.raises(RuntimeError, match=r"compile_commands\.json"):
            adapter.get_lsp_command(tmp_path)
