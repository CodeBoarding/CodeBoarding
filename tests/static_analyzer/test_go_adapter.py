"""Tests for the Go language adapter."""

import shutil
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from static_analyzer.engine.adapters.go_adapter import GoAdapter, _directory_filters_from_ignore_manager
from repo_utils.ignore import RepoIgnoreManager


class TestGetLspCommandGoCheck:
    """``get_lsp_command`` must reject hosts without the Go toolchain.

    gopls silently indexes nothing when ``go`` is missing — symbol and
    reference queries return empty, producing a zero-edge call graph.
    We surface that as a clear RuntimeError at LSP-launch, mirroring
    ``RustAdapter``'s cargo check.
    """

    def test_raises_when_go_missing(self, tmp_path: Path) -> None:
        real_which = shutil.which

        def selective(name: str) -> str | None:
            if name == "go":
                return None
            return real_which(name)

        with patch("static_analyzer.engine.adapters.go_adapter.shutil.which", side_effect=selective):
            with pytest.raises(RuntimeError, match=r"Go toolchain not found.*go\.dev/dl"):
                GoAdapter().get_lsp_command(tmp_path)

    def test_returns_command_when_go_present(self, tmp_path: Path) -> None:
        real_which = shutil.which

        def selective(name: str) -> str | None:
            if name == "go":
                return "/usr/local/bin/go"
            return real_which(name)

        with patch("static_analyzer.engine.adapters.go_adapter.shutil.which", side_effect=selective):
            cmd = GoAdapter().get_lsp_command(tmp_path)
        assert cmd
        assert any("gopls" in part for part in cmd)


class TestBuildTagFiltering:
    """Tests for _has_excluding_build_tag and discover_source_files filtering."""

    def test_detects_go_build_negation(self, tmp_path: Path):
        go_file = tmp_path / "labels_dedupe.go"
        go_file.write_text("//go:build !dedupelabels\n\npackage labels\n")

        assert GoAdapter._has_excluding_build_tag(go_file) is True

    def test_detects_plus_build_negation(self, tmp_path: Path):
        go_file = tmp_path / "old_style.go"
        go_file.write_text("// +build !linux\n\npackage main\n")

        assert GoAdapter._has_excluding_build_tag(go_file) is True

    def test_allows_normal_build_tag(self, tmp_path: Path):
        go_file = tmp_path / "linux_only.go"
        go_file.write_text("//go:build linux\n\npackage main\n")

        assert GoAdapter._has_excluding_build_tag(go_file) is False

    def test_allows_no_build_tag(self, tmp_path: Path):
        go_file = tmp_path / "normal.go"
        go_file.write_text("package main\n\nfunc main() {}\n")

        assert GoAdapter._has_excluding_build_tag(go_file) is False

    def test_allows_empty_file(self, tmp_path: Path):
        go_file = tmp_path / "empty.go"
        go_file.write_text("")

        assert GoAdapter._has_excluding_build_tag(go_file) is False

    def test_handles_comment_before_build_tag(self, tmp_path: Path):
        """Build tags can have regular comments before them."""
        go_file = tmp_path / "commented.go"
        go_file.write_text("// Copyright 2024\n//go:build !stringlabels\n\npackage labels\n")

        assert GoAdapter._has_excluding_build_tag(go_file) is True

    def test_complex_build_expression_with_negation(self, tmp_path: Path):
        go_file = tmp_path / "complex.go"
        go_file.write_text("//go:build (linux && !cgo) || darwin\n\npackage main\n")

        assert GoAdapter._has_excluding_build_tag(go_file) is True

    def test_ignores_build_tag_in_code(self, tmp_path: Path):
        """Build tags after the package declaration are not real build tags."""
        go_file = tmp_path / "fake_tag.go"
        go_file.write_text("package main\n\n// This is not a build tag\n//go:build !fake\n")

        assert GoAdapter._has_excluding_build_tag(go_file) is False


class TestGoplsConfiguration:
    """Tests for gopls init options and environment variables."""

    def test_init_options_include_directory_filters_from_ignore_manager(self, tmp_path: Path):
        """Directory filters are derived from .codeboardingignore patterns."""
        # Create a .codeboardingignore with directory patterns
        cb_dir = tmp_path / ".codeboarding"
        cb_dir.mkdir()
        ignore_file = cb_dir / ".codeboardingignore"
        ignore_file.write_text("vendor/\n**/tests/**\n**/examples/**\n*.test.js\n")

        ignore_manager = RepoIgnoreManager(tmp_path)
        adapter = GoAdapter()
        opts = adapter.get_lsp_init_options(ignore_manager)
        filters = opts["directoryFilters"]

        # Directory patterns should be converted
        assert "-**/vendor" in filters
        assert "-**/tests" in filters
        assert "-**/examples" in filters
        # File patterns should NOT appear
        assert not any("test.js" in f for f in filters)
        # Always-ignored dirs should be included
        assert "-**/node_modules" in filters

    def test_init_options_without_ignore_manager(self):
        """Without ignore manager, still includes always-ignored dirs."""
        adapter = GoAdapter()
        opts = adapter.get_lsp_init_options()
        filters = opts["directoryFilters"]
        assert "-**/node_modules" in filters
        assert "-**/build" in filters

    def test_init_options_disable_all_analyzers(self):
        adapter = GoAdapter()
        opts = adapter.get_lsp_init_options()
        assert opts["analyses"]["all"] is False

    def test_env_sets_gogc(self):
        adapter = GoAdapter()
        env = adapter.get_lsp_env()
        assert env["GOGC"] == "50"

    def test_directory_filters_deduplicates(self, tmp_path: Path):
        """Same directory from multiple patterns should only appear once."""
        cb_dir = tmp_path / ".codeboarding"
        cb_dir.mkdir()
        ignore_file = cb_dir / ".codeboardingignore"
        ignore_file.write_text("vendor/\n**/vendor/**\n")

        ignore_manager = RepoIgnoreManager(tmp_path)
        filters = _directory_filters_from_ignore_manager(ignore_manager)
        vendor_entries = [f for f in filters if "vendor" in f]
        assert len(vendor_entries) == 1
