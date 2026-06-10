"""Tests for the Mojo language adapter."""

import shutil
from pathlib import Path
from unittest.mock import patch

import pytest

from static_analyzer.constants import Language
from static_analyzer.engine.adapters.mojo_adapter import MojoAdapter


class TestGetLspCommandBinaryCheck:

    def test_raises_when_binary_missing(self, tmp_path: Path) -> None:
        with patch("static_analyzer.engine.adapters.mojo_adapter.shutil.which", return_value=None):
            with pytest.raises(RuntimeError, match=r"mojo-lsp-server not found.*pixi"):
                MojoAdapter().get_lsp_command(tmp_path)

    def test_uses_resolved_command_when_present(self, tmp_path: Path) -> None:
        real_which = shutil.which
        resolved = "/opt/codeboarding/pm-tools/mojo/bin/mojo-lsp-server"

        def selective(name: str) -> str | None:
            return resolved if name == resolved else real_which(name)

        lsp_servers = {"mojo": {"command": [resolved]}}
        with (
            patch("static_analyzer.engine.adapters.mojo_adapter.shutil.which", side_effect=selective),
            patch("static_analyzer.engine.language_adapter.get_config", return_value=lsp_servers),
        ):
            cmd = MojoAdapter().get_lsp_command(tmp_path)
        assert cmd == [resolved]

    def test_falls_back_to_bare_command_when_on_path(self, tmp_path: Path) -> None:
        with (
            patch(
                "static_analyzer.engine.adapters.mojo_adapter.shutil.which",
                return_value="/usr/local/bin/mojo-lsp-server",
            ),
            patch("static_analyzer.engine.language_adapter.get_config", return_value={}),
        ):
            cmd = MojoAdapter().get_lsp_command(tmp_path)
        assert cmd == ["mojo-lsp-server"]


class TestMojoAdapterProperties:

    def test_language(self):
        assert MojoAdapter().language == "Mojo"

    def test_language_enum(self):
        assert MojoAdapter().language_enum is Language.MOJO

    def test_file_extensions(self):
        assert MojoAdapter().file_extensions == (".mojo", ".\U0001f525")

    def test_lsp_command(self):
        assert MojoAdapter().lsp_command == ["mojo-lsp-server"]

    def test_language_id(self):
        assert MojoAdapter().language_id == "mojo"

    def test_config_key_defaults_to_language_id(self):
        assert MojoAdapter().config_key == "mojo"


class TestQualifiedNameInherited:
    """Locks in that MojoAdapter does NOT override build_qualified_name —
    Mojo's symbol model is Python-shaped and the base dotted-module logic
    is intentional. Catches accidental overrides that would break call-graph
    edges by emitting differently-shaped qualified names."""

    def test_top_level_function(self, tmp_path: Path) -> None:
        qn = MojoAdapter().build_qualified_name(
            file_path=tmp_path / "pkg" / "service.mojo",
            symbol_name="run",
            symbol_kind=12,
            parent_chain=[],
            project_root=tmp_path,
        )
        assert qn == "pkg.service.run"

    def test_struct_method(self, tmp_path: Path) -> None:
        qn = MojoAdapter().build_qualified_name(
            file_path=tmp_path / "models" / "user.mojo",
            symbol_name="greet",
            symbol_kind=6,
            parent_chain=[("User", 23)],
            project_root=tmp_path,
        )
        assert qn == "models.user.User.greet"


def _sym(name: str, kind: int, line: int, end_line: int | None = None, children: list | None = None) -> dict:
    end = end_line if end_line is not None else line
    return {
        "name": name,
        "kind": kind,
        "range": {"start": {"line": line, "character": 0}, "end": {"line": end, "character": 10}},
        "selectionRange": {"start": {"line": line, "character": 3}, "end": {"line": line, "character": 8}},
        "children": children or [],
    }


class TestPostprocessDocumentSymbols:
    """mojo-lsp-server reports range == selectionRange (declaration line only),
    which defeats call-site containment and yields empty call graphs. The
    adapter must extend each degenerate range to the next sibling's start."""

    def _run(self, symbols: list[dict], tmp_path: Path, n_lines: int = 100) -> list[dict]:
        f = tmp_path / "mod.mojo"
        f.write_text("\n".join(f"# line {i}" for i in range(n_lines)))
        return MojoAdapter().postprocess_document_symbols(symbols, f)

    def test_extends_to_next_sibling(self, tmp_path: Path) -> None:
        syms = self._run([_sym("a", 12, 0), _sym("b", 12, 10)], tmp_path)
        assert syms[0]["range"]["end"]["line"] == 9
        assert syms[1]["range"]["end"]["line"] == 99

    def test_last_sibling_extends_to_file_end(self, tmp_path: Path) -> None:
        syms = self._run([_sym("a", 12, 5)], tmp_path, n_lines=42)
        assert syms[0]["range"]["end"]["line"] == 41

    def test_children_bounded_by_parent(self, tmp_path: Path) -> None:
        struct = _sym("S", 23, 10, children=[_sym("m1", 6, 12), _sym("m2", 6, 20)])
        syms = self._run([struct, _sym("after", 12, 30)], tmp_path)
        assert syms[0]["range"]["end"]["line"] == 29
        m1, m2 = syms[0]["children"]
        assert m1["range"]["end"]["line"] == 19
        assert m2["range"]["end"]["line"] == 29

    def test_real_multi_line_ranges_untouched(self, tmp_path: Path) -> None:
        syms = self._run([_sym("a", 12, 0, end_line=7), _sym("b", 12, 10)], tmp_path)
        assert syms[0]["range"]["end"] == {"line": 7, "character": 10}

    def test_unreadable_file_returns_symbols_unchanged(self, tmp_path: Path) -> None:
        syms = [_sym("a", 12, 0)]
        out = MojoAdapter().postprocess_document_symbols(syms, tmp_path / "missing.mojo")
        assert out[0]["range"]["end"]["line"] == 0
