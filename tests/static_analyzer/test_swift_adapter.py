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
        def selective(name: str) -> str | None:
            return None

        with patch("static_analyzer.engine.adapters.swift_adapter.shutil.which", side_effect=selective):
            with pytest.raises(RuntimeError, match=r"Swift toolchain not found.*swift\.org"):
                SwiftAdapter().get_lsp_command(tmp_path)

    def test_raises_when_sourcekit_lsp_missing(self, tmp_path: Path) -> None:
        """``swift`` present but ``sourcekit-lsp`` absent (partial install / shim layouts)."""

        def selective(name: str) -> str | None:
            if name == "swift":
                return "/usr/local/bin/swift"
            if name == "sourcekit-lsp":
                return None
            return None

        with patch("static_analyzer.engine.adapters.swift_adapter.shutil.which", side_effect=selective):
            with pytest.raises(RuntimeError, match=r"sourcekit-lsp is not"):
                SwiftAdapter().get_lsp_command(tmp_path)

    def test_returns_command_when_both_present(self, tmp_path: Path) -> None:
        def selective(name: str) -> str | None:
            if name == "swift":
                return "/usr/local/bin/swift"
            if name == "sourcekit-lsp":
                return "/usr/local/bin/sourcekit-lsp"
            return None

        with patch("static_analyzer.engine.adapters.swift_adapter.shutil.which", side_effect=selective):
            cmd = SwiftAdapter().get_lsp_command(tmp_path)
        assert cmd
        assert any("sourcekit-lsp" in part for part in cmd)


class TestBuildQualifiedName:
    """SwiftPM convention: ``<package>/Sources/<Target>/...`` and
    ``<package>/Tests/<TestTarget>/...``. The adapter strips the leading
    ``Sources``/``Tests`` segment so the SwiftPM target becomes the top
    component of the qualified name.
    """

    def setup_method(self):
        self.adapter = SwiftAdapter()
        self.root = Path("/project")

    def test_top_level_function_strips_sources(self):
        result = self.adapter.build_qualified_name(
            file_path=Path("/project/Sources/App/main.swift"),
            symbol_name="run",
            symbol_kind=12,
            parent_chain=[],
            project_root=self.root,
        )
        assert result == "App.main.run"

    def test_method_in_class_strips_sources(self):
        result = self.adapter.build_qualified_name(
            file_path=Path("/project/Sources/App/User.swift"),
            symbol_name="greet",
            symbol_kind=6,
            parent_chain=[("User", 5)],
            project_root=self.root,
        )
        assert result == "App.User.User.greet"

    def test_tests_directory_strips_prefix(self):
        result = self.adapter.build_qualified_name(
            file_path=Path("/project/Tests/AppTests/UserTests.swift"),
            symbol_name="testGreet",
            symbol_kind=6,
            parent_chain=[("UserTests", 5)],
            project_root=self.root,
        )
        assert result == "AppTests.UserTests.UserTests.testGreet"

    def test_flat_layout_unchanged(self):
        """Non-SwiftPM layout (no Sources/Tests prefix): falls back to file path."""
        result = self.adapter.build_qualified_name(
            file_path=Path("/project/App/User.swift"),
            symbol_name="greet",
            symbol_kind=6,
            parent_chain=[("User", 5)],
            project_root=self.root,
        )
        assert result == "App.User.User.greet"

    def test_package_for_file_strips_sources(self):
        assert self.adapter.get_package_for_file(Path("/project/Sources/App/User.swift"), self.root) == "App"
