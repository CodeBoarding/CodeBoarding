"""Tests for C/C++ build-system detection."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from static_analyzer.engine.adapters.cpp_cdb import (
    BuildSystemKind,
    detect_build_system,
    install_hint_for,
)


class TestDetectBuildSystem:
    """Marker-file detection; precedence enforced by the ordering in
    :func:`detect_build_system` and asserted by ``test_*_wins_over_*`` below.
    """

    def test_unknown_for_empty_dir(self, tmp_path: Path) -> None:
        assert detect_build_system(tmp_path) is BuildSystemKind.UNKNOWN

    def test_unknown_for_nonexistent_dir(self, tmp_path: Path) -> None:
        assert detect_build_system(tmp_path / "does-not-exist") is BuildSystemKind.UNKNOWN

    def test_compile_commands_json_detected(self, tmp_path: Path) -> None:
        (tmp_path / "compile_commands.json").write_text("[]")
        assert detect_build_system(tmp_path) is BuildSystemKind.COMPILE_COMMANDS_JSON

    def test_compile_flags_txt_detected(self, tmp_path: Path) -> None:
        (tmp_path / "compile_flags.txt").write_text("-std=c++20\n")
        assert detect_build_system(tmp_path) is BuildSystemKind.COMPILE_FLAGS_TXT

    def test_cmake_detected(self, tmp_path: Path) -> None:
        (tmp_path / "CMakeLists.txt").write_text("project(x)")
        assert detect_build_system(tmp_path) is BuildSystemKind.CMAKE

    def test_meson_detected(self, tmp_path: Path) -> None:
        (tmp_path / "meson.build").write_text("project('x')")
        assert detect_build_system(tmp_path) is BuildSystemKind.MESON

    def test_bazel_module_detected(self, tmp_path: Path) -> None:
        (tmp_path / "MODULE.bazel").write_text("module(name='x')")
        assert detect_build_system(tmp_path) is BuildSystemKind.BAZEL

    def test_bazel_workspace_detected(self, tmp_path: Path) -> None:
        (tmp_path / "WORKSPACE").write_text("workspace(name='x')")
        assert detect_build_system(tmp_path) is BuildSystemKind.BAZEL

    def test_autotools_detected(self, tmp_path: Path) -> None:
        (tmp_path / "configure.ac").write_text("AC_INIT")
        assert detect_build_system(tmp_path) is BuildSystemKind.AUTOTOOLS

    def test_ninja_detected(self, tmp_path: Path) -> None:
        (tmp_path / "build.ninja").write_text("rule cc\n")
        assert detect_build_system(tmp_path) is BuildSystemKind.NINJA

    def test_make_detected(self, tmp_path: Path) -> None:
        (tmp_path / "Makefile").write_text("all:\n\techo hi\n")
        assert detect_build_system(tmp_path) is BuildSystemKind.MAKE

    def test_gnumakefile_also_counts_as_make(self, tmp_path: Path) -> None:
        (tmp_path / "GNUmakefile").write_text("all:\n")
        assert detect_build_system(tmp_path) is BuildSystemKind.MAKE

    def test_existing_cdb_wins_over_cmake_markers(self, tmp_path: Path) -> None:
        """A pre-existing ``compile_commands.json`` beats CMake detection:
        we don't want to nag the user to re-run cmake when a valid CDB is
        already on disk.
        """
        (tmp_path / "CMakeLists.txt").write_text("project(x)")
        (tmp_path / "compile_commands.json").write_text("[]")
        assert detect_build_system(tmp_path) is BuildSystemKind.COMPILE_COMMANDS_JSON

    def test_cmake_wins_over_make(self, tmp_path: Path) -> None:
        """CMake projects routinely ship a top-level Makefile wrapper; the
        CMake marker is more informative for the user-facing hint.
        """
        (tmp_path / "CMakeLists.txt").write_text("project(x)")
        (tmp_path / "Makefile").write_text("all:\n")
        assert detect_build_system(tmp_path) is BuildSystemKind.CMAKE

    def test_autotools_wins_over_make(self, tmp_path: Path) -> None:
        """Autotools generates a Makefile after ``./configure`` — the
        Autotools hint is correct, the Make hint would make the user wrap
        the build at the wrong layer.
        """
        (tmp_path / "configure.ac").write_text("AC_INIT")
        (tmp_path / "Makefile").write_text("all:\n")
        assert detect_build_system(tmp_path) is BuildSystemKind.AUTOTOOLS


class TestInstallHintFor:
    """Each hint must name a concrete command. Generic advice like "generate
    a CDB" is worthless to a newbie; we pin that as a test invariant.
    """

    def test_cmake_hint_mentions_export_flag(self) -> None:
        hint = install_hint_for(BuildSystemKind.CMAKE)
        assert "CMAKE_EXPORT_COMPILE_COMMANDS" in hint

    def test_meson_hint_mentions_setup(self) -> None:
        hint = install_hint_for(BuildSystemKind.MESON)
        assert "meson setup" in hint

    def test_bazel_hint_mentions_aquery_or_extractor(self) -> None:
        hint = install_hint_for(BuildSystemKind.BAZEL)
        assert "bazel aquery" in hint or "hedron" in hint.lower()

    def test_bazel_hint_points_to_env_var(self) -> None:
        assert "CODEBOARDING_CPP_GENERATE_CDB" in install_hint_for(BuildSystemKind.BAZEL)

    def test_make_hint_mentions_bear(self) -> None:
        assert "bear" in install_hint_for(BuildSystemKind.MAKE).lower()

    def test_autotools_hint_mentions_configure(self) -> None:
        hint = install_hint_for(BuildSystemKind.AUTOTOOLS)
        assert "configure" in hint.lower()

    def test_unknown_hint_suggests_compile_flags_txt(self) -> None:
        assert "compile_flags.txt" in install_hint_for(BuildSystemKind.UNKNOWN)


class TestInstallHintForWindows:
    """Bear is Unix-only; the Windows branch must redirect users away from it."""

    @pytest.mark.parametrize("kind", [BuildSystemKind.MAKE, BuildSystemKind.AUTOTOOLS])
    def test_windows_hint_redirects_away_from_bear(
        self, kind: BuildSystemKind, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(sys, "platform", "win32")
        hint = install_hint_for(kind)
        assert "Windows" in hint
        assert "CMAKE_EXPORT_COMPILE_COMMANDS" in hint or "compile_flags.txt" in hint

    def test_linux_make_hint_still_mentions_bear(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(sys, "platform", "linux")
        assert "bear" in install_hint_for(BuildSystemKind.MAKE).lower()

    def test_linux_autotools_hint_still_mentions_configure(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(sys, "platform", "linux")
        assert "configure" in install_hint_for(BuildSystemKind.AUTOTOOLS).lower()
