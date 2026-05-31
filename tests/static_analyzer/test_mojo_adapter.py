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
        assert MojoAdapter().file_extensions == (".mojo",)

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
