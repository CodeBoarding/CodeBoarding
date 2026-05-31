"""Tests for the Swift language adapter."""

import shutil
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from static_analyzer.constants import Language
from static_analyzer.engine.adapters import get_adapter
from static_analyzer.engine.adapters.swift_adapter import SwiftAdapter, resolve_sourcekit_lsp


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


class TestResolveSourcekitLsp:
    """``resolve_sourcekit_lsp`` is the choke point for PATH + xcrun fallback.

    Cached for one process, so each test clears the cache to stay independent.
    """

    def setup_method(self) -> None:
        resolve_sourcekit_lsp.cache_clear()

    def teardown_method(self) -> None:
        resolve_sourcekit_lsp.cache_clear()

    def test_returns_path_when_on_path(self) -> None:
        with patch("static_analyzer.engine.adapters.swift_adapter.shutil.which", return_value="/usr/bin/sourcekit-lsp"):
            assert resolve_sourcekit_lsp() == "/usr/bin/sourcekit-lsp"

    def test_returns_none_on_linux_when_not_on_path(self) -> None:
        with patch("static_analyzer.engine.adapters.swift_adapter.shutil.which", return_value=None):
            with patch("static_analyzer.engine.adapters.swift_adapter.platform.system", return_value="Linux"):
                assert resolve_sourcekit_lsp() is None

    def test_returns_none_on_windows_when_not_on_path(self) -> None:
        with patch("static_analyzer.engine.adapters.swift_adapter.shutil.which", return_value=None):
            with patch("static_analyzer.engine.adapters.swift_adapter.platform.system", return_value="Windows"):
                assert resolve_sourcekit_lsp() is None

    def test_falls_back_to_xcrun_on_macos(self, tmp_path: Path) -> None:
        """When PATH is empty, ``xcrun --find`` reaches the Xcode/CLT binary."""
        fake_binary = tmp_path / "sourcekit-lsp"
        fake_binary.write_text("")  # exists, so Path.is_file() passes
        mock_proc = MagicMock(returncode=0, stdout=f"{fake_binary}\n", stderr="")

        with patch("static_analyzer.engine.adapters.swift_adapter.shutil.which", return_value=None):
            with patch("static_analyzer.engine.adapters.swift_adapter.platform.system", return_value="Darwin"):
                with patch(
                    "static_analyzer.engine.adapters.swift_adapter.subprocess.run", return_value=mock_proc
                ) as run_mock:
                    assert resolve_sourcekit_lsp() == str(fake_binary)
                    assert run_mock.call_args.args[0] == ["xcrun", "--find", "sourcekit-lsp"]

    def test_xcrun_returns_nonexistent_path_yields_none(self) -> None:
        """A malformed xcrun reply must not produce a path we'd later fail to spawn."""
        mock_proc = MagicMock(returncode=0, stdout="/nope/sourcekit-lsp\n", stderr="")
        with patch("static_analyzer.engine.adapters.swift_adapter.shutil.which", return_value=None):
            with patch("static_analyzer.engine.adapters.swift_adapter.platform.system", return_value="Darwin"):
                with patch("static_analyzer.engine.adapters.swift_adapter.subprocess.run", return_value=mock_proc):
                    assert resolve_sourcekit_lsp() is None

    def test_xcrun_nonzero_yields_none(self) -> None:
        mock_proc = MagicMock(returncode=72, stdout="", stderr="xcrun: error: unable to find utility")
        with patch("static_analyzer.engine.adapters.swift_adapter.shutil.which", return_value=None):
            with patch("static_analyzer.engine.adapters.swift_adapter.platform.system", return_value="Darwin"):
                with patch("static_analyzer.engine.adapters.swift_adapter.subprocess.run", return_value=mock_proc):
                    assert resolve_sourcekit_lsp() is None

    def test_xcrun_missing_yields_none(self) -> None:
        """Bare-bones macOS install without Developer Tools: ``xcrun`` itself missing."""
        with patch("static_analyzer.engine.adapters.swift_adapter.shutil.which", return_value=None):
            with patch("static_analyzer.engine.adapters.swift_adapter.platform.system", return_value="Darwin"):
                with patch(
                    "static_analyzer.engine.adapters.swift_adapter.subprocess.run", side_effect=FileNotFoundError()
                ):
                    assert resolve_sourcekit_lsp() is None

    def test_xcrun_timeout_yields_none(self) -> None:
        with patch("static_analyzer.engine.adapters.swift_adapter.shutil.which", return_value=None):
            with patch("static_analyzer.engine.adapters.swift_adapter.platform.system", return_value="Darwin"):
                with patch(
                    "static_analyzer.engine.adapters.swift_adapter.subprocess.run",
                    side_effect=subprocess.TimeoutExpired(cmd="xcrun", timeout=10),
                ):
                    assert resolve_sourcekit_lsp() is None


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
            return None

        with patch("static_analyzer.engine.adapters.swift_adapter.shutil.which", side_effect=selective):
            with patch("static_analyzer.engine.adapters.swift_adapter.resolve_sourcekit_lsp", return_value=None):
                with pytest.raises(RuntimeError, match=r"sourcekit-lsp could not be located"):
                    SwiftAdapter().get_lsp_command(tmp_path)

    def test_returns_command_when_both_present(self, tmp_path: Path) -> None:
        def selective(name: str) -> str | None:
            if name == "swift":
                return "/usr/local/bin/swift"
            if name == "sourcekit-lsp":
                return "/usr/local/bin/sourcekit-lsp"
            return None

        with patch("static_analyzer.engine.adapters.swift_adapter.shutil.which", side_effect=selective):
            with patch(
                "static_analyzer.engine.adapters.swift_adapter.resolve_sourcekit_lsp",
                return_value="/usr/local/bin/sourcekit-lsp",
            ):
                cmd = SwiftAdapter().get_lsp_command(tmp_path)
        assert cmd
        assert any("sourcekit-lsp" in part for part in cmd)

    def test_returns_command_via_xcrun_when_not_on_path(self, tmp_path: Path) -> None:
        """macOS users with Xcode but no /usr/bin shim: xcrun fallback supplies the path."""
        xcrun_path = (
            "/Applications/Xcode.app/Contents/Developer/Toolchains/XcodeDefault.xctoolchain/usr/bin/sourcekit-lsp"
        )

        def selective(name: str) -> str | None:
            if name == "swift":
                return "/usr/bin/swift"
            return None  # sourcekit-lsp not on PATH

        with patch("static_analyzer.engine.adapters.swift_adapter.shutil.which", side_effect=selective):
            with patch(
                "static_analyzer.engine.adapters.swift_adapter.resolve_sourcekit_lsp",
                return_value=xcrun_path,
            ):
                cmd = SwiftAdapter().get_lsp_command(tmp_path)
        assert cmd[0] == xcrun_path


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
