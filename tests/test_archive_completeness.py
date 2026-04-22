"""Unit tests for the ``archive_is_complete`` helper + binary-path resolution.

The old marker-only check treated a half-extracted archive as "installed",
which caused ``resolve_config`` to emit ``command[0]`` pointing at a
binary that didn't exist. These tests pin the completeness contract.
"""

from __future__ import annotations

import platform
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tool_registry.manifest import archive_binary_path, archive_is_complete
from tool_registry.registry import (
    ConfigSection,
    GitHubToolSource,
    ToolDependency,
    ToolKind,
)


def _make_archive_dep(
    key: str = "cpp",
    binary_name: str = "clangd",
    archive_subdir: str = "clangd",
    archive_marker: str = "bin",
    archive_binary_path: str = "bin/clangd",
) -> ToolDependency:
    return ToolDependency(
        key=key,
        binary_name=binary_name,
        kind=ToolKind.ARCHIVE,
        config_section=ConfigSection.LSP_SERVERS,
        source=GitHubToolSource(repo="example/example", tag="v1", asset_template="asset.zip"),
        archive_subdir=archive_subdir,
        archive_marker=archive_marker,
        archive_binary_path=archive_binary_path,
    )


class TestArchiveIsComplete(unittest.TestCase):
    def test_returns_true_when_marker_and_binary_both_present(self):
        dep = _make_archive_dep()
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            archive_dir = base / "bin" / dep.archive_subdir
            (archive_dir / dep.archive_marker).mkdir(parents=True)
            binary = archive_dir / dep.archive_binary_path
            binary.parent.mkdir(parents=True, exist_ok=True)
            binary.write_text("#!/bin/sh\n")
            self.assertTrue(archive_is_complete(dep, base))

    def test_returns_false_when_archive_dir_missing(self):
        dep = _make_archive_dep()
        with tempfile.TemporaryDirectory() as tmp:
            self.assertFalse(archive_is_complete(dep, Path(tmp)))

    def test_returns_false_when_marker_missing(self):
        dep = _make_archive_dep()
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            archive_dir = base / "bin" / dep.archive_subdir
            archive_dir.mkdir(parents=True)
            # No marker dir.
            self.assertFalse(archive_is_complete(dep, base))

    def test_returns_false_when_binary_missing(self):
        """Regression: the half-extracted case the marker-only check got wrong."""
        dep = _make_archive_dep()
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            archive_dir = base / "bin" / dep.archive_subdir
            (archive_dir / dep.archive_marker).mkdir(parents=True)
            # Marker exists, binary does NOT.
            self.assertFalse(archive_is_complete(dep, base))

    def test_returns_true_when_binary_not_declared(self):
        """JDTLS has no ``archive_binary_path`` — marker alone is enough."""
        dep = _make_archive_dep(archive_binary_path="", archive_marker="plugins")
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            archive_dir = base / "bin" / dep.archive_subdir
            (archive_dir / dep.archive_marker).mkdir(parents=True)
            self.assertTrue(archive_is_complete(dep, base))

    def test_non_archive_dep_always_complete(self):
        """NATIVE/NODE/PACKAGE_MANAGER deps are someone else's problem."""
        native_dep = ToolDependency(
            key="gopls",
            binary_name="gopls",
            kind=ToolKind.NATIVE,
            config_section=ConfigSection.LSP_SERVERS,
            source=None,
        )
        with tempfile.TemporaryDirectory() as tmp:
            self.assertTrue(archive_is_complete(native_dep, Path(tmp)))


class TestArchiveBinaryPath(unittest.TestCase):
    def test_returns_none_when_no_binary_path(self):
        dep = _make_archive_dep(archive_binary_path="")
        self.assertIsNone(archive_binary_path(dep, Path("/base")))

    def test_windows_exe_suffix_applied(self):
        dep = _make_archive_dep(archive_binary_path="bin/clangd")
        with patch("tool_registry.manifest.platform.system", return_value="Windows"):
            resolved = archive_binary_path(dep, Path("/base"))
        self.assertIsNotNone(resolved)
        assert resolved is not None  # for mypy
        self.assertTrue(resolved.name.endswith(".exe"))

    def test_non_windows_no_exe_suffix(self):
        dep = _make_archive_dep(archive_binary_path="bin/clangd")
        with patch("tool_registry.manifest.platform.system", return_value="Darwin"):
            resolved = archive_binary_path(dep, Path("/base"))
        self.assertIsNotNone(resolved)
        assert resolved is not None
        self.assertEqual(resolved.name, "clangd")

    def test_honors_explicit_suffix(self):
        """Devs sometimes embed ``.exe`` in the archive_binary_path itself —
        the helper must not stack a second ``.exe``."""
        dep = _make_archive_dep(archive_binary_path="bin/clangd.exe")
        with patch("tool_registry.manifest.platform.system", return_value="Windows"):
            resolved = archive_binary_path(dep, Path("/base"))
        self.assertIsNotNone(resolved)
        assert resolved is not None
        self.assertEqual(resolved.name, "clangd.exe")


if __name__ == "__main__":
    unittest.main()
