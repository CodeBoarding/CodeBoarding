"""Tests for the Go language adapter."""

from pathlib import Path

from static_analyzer.engine.adapters.go_adapter import GoAdapter


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
