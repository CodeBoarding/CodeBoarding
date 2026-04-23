"""Unit tests for the ``archive_is_complete`` helper + binary-path resolution.

The old marker-only check treated a half-extracted archive as "installed",
which caused ``resolve_config`` to emit ``command[0]`` pointing at a
binary that didn't exist. These tests pin the completeness contract.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tool_registry.manifest import archive_binary_path, archive_is_complete, archive_layout_spec
from tool_registry.registry import (
    ArchiveLayout,
    ConfigSection,
    GitHubToolSource,
    ToolDependency,
    ToolKind,
)


def _make_archive_dep(
    key: str = "cpp",
    binary_name: str = "clangd",
    archive_subdir: str = "clangd",
    archive_layout: ArchiveLayout = ArchiveLayout.STRIPPED_BIN_DIR,
) -> ToolDependency:
    return ToolDependency(
        key=key,
        binary_name=binary_name,
        kind=ToolKind.ARCHIVE,
        config_section=ConfigSection.LSP_SERVERS,
        source=GitHubToolSource(repo="example/example", tag="v1", asset_template="asset.zip"),
        archive_subdir=archive_subdir,
        archive_layout=archive_layout,
    )


class TestArchiveIsComplete(unittest.TestCase):
    def test_returns_true_when_marker_and_binary_both_present(self):
        dep = _make_archive_dep()
        marker, _strip, rel = archive_layout_spec(dep)
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            archive_dir = base / "bin" / dep.archive_subdir
            (archive_dir / marker).mkdir(parents=True)
            binary = archive_dir / rel
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
        marker, _strip, _rel = archive_layout_spec(dep)
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            archive_dir = base / "bin" / dep.archive_subdir
            (archive_dir / marker).mkdir(parents=True)
            # Marker exists, binary does NOT.
            self.assertFalse(archive_is_complete(dep, base))

    def test_returns_true_when_layout_has_no_binary(self):
        """NESTED_PLUGINS (JDTLS) has no binary rewrite — marker alone is enough."""
        dep = _make_archive_dep(archive_layout=ArchiveLayout.NESTED_PLUGINS)
        marker, _strip, _rel = archive_layout_spec(dep)
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            archive_dir = base / "bin" / dep.archive_subdir
            (archive_dir / marker).mkdir(parents=True)
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
    def test_returns_none_for_nested_plugins_layout(self):
        dep = _make_archive_dep(archive_layout=ArchiveLayout.NESTED_PLUGINS)
        self.assertIsNone(archive_binary_path(dep, Path("/base")))

    def test_windows_exe_suffix_applied(self):
        dep = _make_archive_dep()
        with patch("tool_registry.manifest.platform.system", return_value="Windows"):
            resolved = archive_binary_path(dep, Path("/base"))
        self.assertIsNotNone(resolved)
        assert resolved is not None  # for mypy
        self.assertTrue(resolved.name.endswith(".exe"))

    def test_non_windows_no_exe_suffix(self):
        dep = _make_archive_dep()
        with patch("tool_registry.manifest.platform.system", return_value="Darwin"):
            resolved = archive_binary_path(dep, Path("/base"))
        self.assertIsNotNone(resolved)
        assert resolved is not None
        self.assertEqual(resolved.name, "clangd")


class TestArchiveLayoutSpec(unittest.TestCase):
    def test_nested_plugins_spec(self):
        dep = _make_archive_dep(archive_layout=ArchiveLayout.NESTED_PLUGINS)
        marker, strip_root, binary = archive_layout_spec(dep)
        self.assertEqual(marker, "plugins")
        self.assertFalse(strip_root)
        self.assertEqual(binary, "")

    def test_stripped_bin_dir_spec_derives_binary_from_dep(self):
        dep = _make_archive_dep(binary_name="clangd", archive_layout=ArchiveLayout.STRIPPED_BIN_DIR)
        marker, strip_root, binary = archive_layout_spec(dep)
        self.assertEqual(marker, "bin")
        self.assertTrue(strip_root)
        self.assertEqual(binary, "bin/clangd")


if __name__ == "__main__":
    unittest.main()
