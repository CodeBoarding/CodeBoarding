"""Tests for the Swift language adapter."""

import shutil
from pathlib import Path
from unittest.mock import patch

import pytest

from static_analyzer.constants import Language
from static_analyzer.engine.adapters import get_adapter
from static_analyzer.engine.adapters.swift_adapter import SwiftAdapter


class TestSwiftAdapterProperties:
    def test_language(self):
        assert SwiftAdapter().language == "Swift"

    def test_file_extensions(self):
        assert SwiftAdapter().file_extensions == (".swift",)

    def test_language_enum(self):
        assert SwiftAdapter().language_enum is Language.SWIFT

    def test_lsp_command(self):
        assert SwiftAdapter().lsp_command == ["sourcekit-lsp"]

    def test_language_id(self):
        assert SwiftAdapter().language_id == "swift"

    def test_registry_returns_swift_adapter(self):
        assert isinstance(get_adapter("Swift"), SwiftAdapter)


class TestGetLspCommandToolchainCheck:
    """sourcekit-lsp ships inside the Swift toolchain; a missing ``swift``
    means a missing LSP. Surface that as a clear error at launch instead of
    a silently empty analysis (mirrors Rust's cargo check and Go's go check).
    """

    def test_raises_when_swift_missing(self, tmp_path: Path) -> None:
        real_which = shutil.which

        def selective(name: str) -> str | None:
            if name == "swift":
                return None
            return real_which(name)

        with patch("static_analyzer.engine.adapters.swift_adapter.shutil.which", side_effect=selective):
            with pytest.raises(RuntimeError, match=r"Swift toolchain not found.*swift\.org"):
                SwiftAdapter().get_lsp_command(tmp_path)

    def test_returns_command_when_swift_present(self, tmp_path: Path) -> None:
        real_which = shutil.which

        def selective(name: str) -> str | None:
            if name == "swift":
                return "/usr/local/bin/swift"
            return real_which(name)

        with patch("static_analyzer.engine.adapters.swift_adapter.shutil.which", side_effect=selective):
            cmd = SwiftAdapter().get_lsp_command(tmp_path)
        assert cmd
        assert any("sourcekit-lsp" in part for part in cmd)


class TestBuildQualifiedName:
    """SwiftAdapter inherits the base implementation; these tests pin the
    expected shape so a future override doesn't silently change consumer output.
    """

    def setup_method(self):
        self.adapter = SwiftAdapter()
        self.root = Path("/project")

    def test_top_level_function(self):
        result = self.adapter.build_qualified_name(
            file_path=Path("/project/Sources/App/main.swift"),
            symbol_name="run",
            symbol_kind=12,
            parent_chain=[],
            project_root=self.root,
        )
        assert result == "Sources.App.main.run"

    def test_method_in_class(self):
        result = self.adapter.build_qualified_name(
            file_path=Path("/project/Sources/App/User.swift"),
            symbol_name="greet",
            symbol_kind=6,
            parent_chain=[("User", 5)],
            project_root=self.root,
        )
        assert result == "Sources.App.User.User.greet"
