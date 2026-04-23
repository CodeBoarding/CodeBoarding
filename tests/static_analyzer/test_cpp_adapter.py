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
from static_analyzer.engine.adapters.cpp_cdb import locate_generated_cdb, locate_user_cdb
from static_analyzer.engine.adapters.cpp_cdb.base import BuildSystemKind


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
        (tmp_path / "compile_commands.json").write_text("[]")
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
        (tmp_path / "compile_commands.json").write_text("[]")
        assert locate_user_cdb(tmp_path) == tmp_path

    def test_find_user_cdb_in_build(self, tmp_path: Path) -> None:
        (tmp_path / "build").mkdir()
        (tmp_path / "build" / "compile_commands.json").write_text("[]")
        assert locate_user_cdb(tmp_path) == tmp_path / "build"

    def test_user_cdb_does_not_match_generated_dir(self, tmp_path: Path) -> None:
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
    def test_empty_detail_returns_none(self) -> None:
        assert _extract_signature("") is None

    def test_no_paren_returns_none(self) -> None:
        assert _extract_signature("int") is None

    def test_empty_params_returns_none(self) -> None:
        assert _extract_signature("() const -> void") is None

    def test_single_param(self) -> None:
        assert _extract_signature("(int) -> void") == "(int)"

    def test_multiple_params(self) -> None:
        assert _extract_signature("(int, const std::string &) -> bool") == "(int, const std.string&)"

    def test_strips_whitespace_before_ref_and_pointer(self) -> None:
        assert _extract_signature("(const Entity &) const") == "(const Entity&)"

    def test_collapses_double_spaces(self) -> None:
        assert _extract_signature("(int   x,   int  y)") == "(int x, int y)"

    def test_nested_template_args(self) -> None:
        """Outer ``(...)`` must balance despite inner ``<``/``>``; template args
        in the signature body are kept (adapter strips them on the symbol name,
        not on detail).
        """
        assert _extract_signature("(std::vector<int> &)") == "(std.vector<int>&)"

    def test_unbalanced_returns_none(self) -> None:
        assert _extract_signature("(int, missing_close") is None


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
        assert param_ctor == "models.Entity.Entity(const std.string&)"
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

    def test_free_function_uses_bare_symbol(self, tmp_path: Path) -> None:
        """Free function (no parent chain) uses just the bare symbol.

        Why: header/source path disagreement would break cross-file refs;
        bare aliases must stay shorter than the scoped canonical so
        CallGraph's longest-wins dedup picks the scoped entry.
        """
        src = tmp_path / "src" / "util.cpp"
        src.parent.mkdir()
        src.touch()
        result = self._call(src, "helper", [], tmp_path)
        assert result == "helper"

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
        src = tmp_path / "a.cpp"
        src.touch()
        result = self._call(src, "std::move", [], tmp_path)
        assert result == "std.move"

    def test_template_symbol_no_parents(self, tmp_path: Path) -> None:
        src = tmp_path / "a.cpp"
        src.touch()
        result = self._call(src, "swap<T>", [], tmp_path)
        assert result == "swap"

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
        (tmp_path / "build" / "Release" / "compile_commands.json").write_text("[]")
        cmd = CppAdapter().get_lsp_command(tmp_path)
        assert cmd

    def test_cmake_build_release(self, tmp_path: Path) -> None:
        (tmp_path / "cmake-build-release").mkdir()
        (tmp_path / "cmake-build-release" / "compile_commands.json").write_text("[]")
        cmd = CppAdapter().get_lsp_command(tmp_path)
        assert cmd

    def test_both_compile_db_and_flags(self, tmp_path: Path) -> None:
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
        src = tmp_path / "a.cpp"
        src.touch()
        result = self._call(
            src,
            "method",
            [("  ", int(NodeType.NAMESPACE)), ("Foo", int(NodeType.CLASS))],
            tmp_path,
        )
        assert result == "Foo.method"


class TestPrepareProjectSkipConditions:
    def test_skip_when_user_cdb_already_present(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """A user-owned compile_commands.json short-circuits even with opt-in set."""
        monkeypatch.setenv("CODEBOARDING_CPP_GENERATE_CDB", "1")
        (tmp_path / "compile_commands.json").write_text("[]")
        with patch("static_analyzer.engine.adapters.cpp_cdb.generator_for") as gen_for:
            CppAdapter().prepare_project(tmp_path)
        gen_for.assert_not_called()

    def test_generated_cdb_does_not_short_circuit_generator(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A stale CDB under ``.codeboarding/cdb/`` must NOT skip the generator —
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
        with patch("static_analyzer.engine.adapters.cpp_cdb.generator_for", return_value=fake_generator):
            CppAdapter().prepare_project(tmp_path)
        fake_generator.generate.assert_called_once_with(tmp_path)

    def test_skip_when_optin_not_set(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("CODEBOARDING_CPP_GENERATE_CDB", raising=False)
        (tmp_path / "Makefile").write_text("all:\n")
        with patch("static_analyzer.engine.adapters.cpp_cdb.generator_for") as gen_for:
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
        with patch("static_analyzer.engine.adapters.cpp_cdb.generator_for", return_value=fake_generator):
            CppAdapter().prepare_project(tmp_path)
        fake_generator.generate.assert_called_once_with(tmp_path)

    def test_generator_failure_is_swallowed(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        """A failing generator must log but not raise — ``get_lsp_command`` owns
        the user-facing error surface.
        """
        monkeypatch.setenv("CODEBOARDING_CPP_GENERATE_CDB", "1")
        (tmp_path / "Makefile").write_text("all:\n")

        fake_generator = MagicMock()
        fake_generator.generate.side_effect = RuntimeError("make exploded")
        fake_generator.kind = BuildSystemKind.MAKE
        with patch("static_analyzer.engine.adapters.cpp_cdb.generator_for", return_value=fake_generator):
            CppAdapter().prepare_project(tmp_path)  # Must NOT raise
        assert "CDB generation failed" in caplog.text
