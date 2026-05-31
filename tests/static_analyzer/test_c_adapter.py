"""Tests for the C language adapter (clangd backend, shared with C++)."""

from pathlib import Path

import pytest

from static_analyzer.constants import Language, NodeType
from static_analyzer.engine.adapters import get_adapter
from static_analyzer.engine.adapters.c_adapter import CAdapter
from static_analyzer.engine.adapters.cpp_adapter import CppAdapter


class TestCAdapterProperties:
    @pytest.mark.parametrize(
        "attr, expected",
        [
            ("language", "C"),
            ("language_id", "c"),
            ("config_key", "cpp"),
            ("lsp_command", ["clangd"]),
        ],
    )
    def test_trivial_getters(self, attr: str, expected: object) -> None:
        assert getattr(CAdapter(), attr) == expected

    def test_language_enum(self):
        assert CAdapter().language_enum is Language.C

    def test_file_extensions(self):
        assert set(CAdapter().file_extensions) == {".c", ".h"}

    def test_registry_returns_c_adapter(self):
        assert isinstance(get_adapter("C"), CAdapter)

    def test_subclasses_cpp_adapter(self):
        """CAdapter inherits all clangd behaviour (CDB check, phase-1 timeout,
        qualified-name building) from CppAdapter — only surface labels and
        ``config_key`` differ. Pin the relationship so a future refactor
        doesn't accidentally break the shared backend."""
        assert issubclass(CAdapter, CppAdapter)

    @pytest.mark.parametrize("ext", [".c", ".h"])
    def test_language_id_for_file_always_c(self, ext: str) -> None:
        """CAdapter only ever sees C files; the override is for explicitness."""
        assert CAdapter().language_id_for_file(Path(f"foo{ext}")) == "c"

    def test_c_adapter_still_returns_c_for_h_in_pure_c_projects(self) -> None:
        """HIGH#1 pure-C invariant: even though ``CppAdapter`` now maps ``.h``
        to ``"cpp"`` (the common case in mixed repos), ``CAdapter``'s override
        must keep ``.h`` -> ``"c"`` so pure-C kernels / libcurl-style repos
        don't suddenly have their public headers parsed as C++.
        """
        adapter = CAdapter()
        assert adapter.language_id_for_file(Path("api.h")) == "c"
        assert adapter.language_id_for_file(Path("impl.c")) == "c"

    def test_c_adapter_fallback_flags_use_c_standard(self) -> None:
        """Pure-C projects with no CDB must NOT inherit CppAdapter's ``-std=c++20``."""
        opts = CAdapter().get_lsp_init_options()
        assert opts == {"fallbackFlags": ["-std=c17"]}
        assert "-std=c++20" not in opts["fallbackFlags"]


class TestCompilationDatabaseGuardInherited:
    """The CDB requirement is inherited from CppAdapter; verify the inherited
    behaviour still fires (pure-C repos must surface the same actionable error
    when no compile_commands.json exists)."""

    def test_raises_when_no_compilation_database(self, tmp_path: Path) -> None:
        with pytest.raises(RuntimeError, match=r"compile_commands\.json"):
            CAdapter().get_lsp_command(tmp_path)

    def test_accepts_compile_flags_txt_at_root(self, tmp_path: Path) -> None:
        (tmp_path / "compile_flags.txt").write_text("-std=c11\n")
        cmd = CAdapter().get_lsp_command(tmp_path)
        assert cmd

    def test_inherits_file_stem_prefix_for_no_parent_globals(self, tmp_path: Path) -> None:
        """M11: CAdapter must inherit the file-stem fallback from CppAdapter,
        so two ``add`` helpers in different C TUs don't collide in SymbolTable.
        """
        src = tmp_path / "src" / "math_utils.c"
        src.parent.mkdir(parents=True)
        src.touch()
        qname = CAdapter().build_qualified_name(
            file_path=src,
            symbol_name="add",
            symbol_kind=int(NodeType.FUNCTION),
            parent_chain=[],
            project_root=tmp_path,
        )
        assert qname == "math_utils.add"
