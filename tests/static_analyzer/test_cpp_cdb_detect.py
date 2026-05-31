"""Tests for C/C++ build-system detection."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from static_analyzer.cdb import (
    BuildSystemKind,
    DetectionResult,
    detect_build_system,
    install_hint_for,
    locate_user_cdb,
)


class TestDetectBuildSystem:
    """Marker-file detection; precedence enforced by the ordering in
    :func:`detect_build_system` and asserted by ``test_*_wins_over_*`` below.
    """

    def test_unknown_for_empty_dir(self, tmp_path: Path) -> None:
        result = detect_build_system(tmp_path)
        assert result.kind is BuildSystemKind.UNKNOWN
        assert result.existing_cdb is None

    def test_unknown_for_nonexistent_dir(self, tmp_path: Path) -> None:
        result = detect_build_system(tmp_path / "does-not-exist")
        assert result.kind is BuildSystemKind.UNKNOWN
        assert result.existing_cdb is None

    def test_existing_compile_commands_json_surfaces_via_existing_cdb(self, tmp_path: Path) -> None:
        """Valid pre-existing CDB short-circuits via ``existing_cdb``; ``kind``
        stays UNKNOWN because there's no buildable system to drive."""
        (tmp_path / "compile_commands.json").write_text(
            '[{"directory": ".", "file": "x.cc", "command": "c++ -c x.cc"}]'
        )
        result = detect_build_system(tmp_path)
        assert result.kind is BuildSystemKind.UNKNOWN
        assert result.existing_cdb == tmp_path

    def test_existing_compile_flags_txt_surfaces_via_existing_cdb(self, tmp_path: Path) -> None:
        (tmp_path / "compile_flags.txt").write_text("-std=c++20\n")
        result = detect_build_system(tmp_path)
        assert result.existing_cdb == tmp_path

    def test_cmake_detected(self, tmp_path: Path) -> None:
        (tmp_path / "CMakeLists.txt").write_text("project(x)")
        assert detect_build_system(tmp_path).kind is BuildSystemKind.CMAKE

    def test_meson_detected(self, tmp_path: Path) -> None:
        (tmp_path / "meson.build").write_text("project('x')")
        assert detect_build_system(tmp_path).kind is BuildSystemKind.MESON

    def test_bazel_module_detected(self, tmp_path: Path) -> None:
        (tmp_path / "MODULE.bazel").write_text("module(name='x')")
        assert detect_build_system(tmp_path).kind is BuildSystemKind.BAZEL

    def test_bazel_workspace_detected(self, tmp_path: Path) -> None:
        (tmp_path / "WORKSPACE").write_text("workspace(name='x')")
        assert detect_build_system(tmp_path).kind is BuildSystemKind.BAZEL

    def test_autotools_detected(self, tmp_path: Path) -> None:
        (tmp_path / "configure.ac").write_text("AC_INIT")
        assert detect_build_system(tmp_path).kind is BuildSystemKind.AUTOTOOLS

    def test_ninja_detected(self, tmp_path: Path) -> None:
        (tmp_path / "build.ninja").write_text("rule cc\n")
        assert detect_build_system(tmp_path).kind is BuildSystemKind.NINJA

    def test_make_detected(self, tmp_path: Path) -> None:
        (tmp_path / "Makefile").write_text("all:\n\techo hi\n")
        result = detect_build_system(tmp_path)
        assert result.kind is BuildSystemKind.MAKE
        assert result.build_root == tmp_path

    def test_gnumakefile_also_counts_as_make(self, tmp_path: Path) -> None:
        (tmp_path / "GNUmakefile").write_text("all:\n")
        assert detect_build_system(tmp_path).kind is BuildSystemKind.MAKE

    def test_existing_cdb_does_not_block_buildable_detection(self, tmp_path: Path) -> None:
        """Pre-existing valid CDB and CMakeLists.txt: both surface so the
        caller can pick (user CDB wins for ``ensure_cdb``, kind drives hints)."""
        (tmp_path / "CMakeLists.txt").write_text("project(x)")
        (tmp_path / "compile_commands.json").write_text(
            '[{"directory": ".", "file": "x.cc", "command": "c++ -c x.cc"}]'
        )
        result = detect_build_system(tmp_path)
        assert result.kind is BuildSystemKind.CMAKE
        assert result.existing_cdb == tmp_path

    def test_cmake_wins_over_make(self, tmp_path: Path) -> None:
        """CMake projects routinely ship a top-level Makefile wrapper; the
        CMake marker is more informative for the user-facing hint.
        """
        (tmp_path / "CMakeLists.txt").write_text("project(x)")
        (tmp_path / "Makefile").write_text("all:\n")
        assert detect_build_system(tmp_path).kind is BuildSystemKind.CMAKE

    def test_autotools_wins_over_make(self, tmp_path: Path) -> None:
        """Autotools generates a Makefile after ``./configure`` -- the
        Autotools hint is correct, the Make hint would make the user wrap
        the build at the wrong layer.
        """
        (tmp_path / "configure.ac").write_text("AC_INIT")
        (tmp_path / "Makefile").write_text("all:\n")
        assert detect_build_system(tmp_path).kind is BuildSystemKind.AUTOTOOLS

    def test_stockfish_shape_makefile_in_src_detected(self, tmp_path: Path) -> None:
        """Stockfish-shape: Makefile in ``src/`` is the build cwd, not root."""
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "Makefile").write_text("all:\n")
        result = detect_build_system(tmp_path)
        assert result.kind is BuildSystemKind.MAKE
        assert result.build_root == tmp_path / "src"

    def test_root_wins_over_subdir(self, tmp_path: Path) -> None:
        """Root probe order beats subdir; nested marker only fires when root is bare."""
        (tmp_path / "Makefile").write_text("all:\n")
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "Makefile").write_text("all:\n")
        result = detect_build_system(tmp_path)
        assert result.build_root == tmp_path

    def test_cmake_in_build_subdir_detected(self, tmp_path: Path) -> None:
        """CMake CDB output is conventionally under build/; the marker may live there."""
        (tmp_path / "build").mkdir()
        (tmp_path / "build" / "CMakeLists.txt").write_text("project(x)")
        result = detect_build_system(tmp_path)
        assert result.kind is BuildSystemKind.CMAKE
        assert result.build_root == tmp_path / "build"


class TestLocateUserCdb:
    """User-CDB probe must walk the same subdir list as detection (H3)."""

    def test_finds_compile_flags_in_src_subdir(self, tmp_path: Path) -> None:
        """``src/compile_flags.txt`` is hand-authored by users with Stockfish-shape
        repos. The probe must include ``src``."""
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "compile_flags.txt").write_text("-std=c++20\n")
        assert locate_user_cdb(tmp_path) == tmp_path / "src"

    def test_finds_compile_commands_in_build_subdir(self, tmp_path: Path) -> None:
        (tmp_path / "build").mkdir()
        (tmp_path / "build" / "compile_commands.json").write_text(
            '[{"directory": ".", "file": "x.cc", "command": "c++ -c x.cc"}]'
        )
        assert locate_user_cdb(tmp_path) == tmp_path / "build"

    def test_skips_invalid_user_cdb_with_warning(self, tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
        """Malformed user ``compile_commands.json`` (M2) must warn and fall through."""
        (tmp_path / "compile_commands.json").write_text("not json")
        result = locate_user_cdb(tmp_path)
        assert result is None
        assert "Ignoring user compile_commands.json" in caplog.text

    def test_skips_empty_user_cdb(self, tmp_path: Path) -> None:
        """Empty array (M2/M3) is not a usable CDB."""
        (tmp_path / "compile_commands.json").write_text("[]")
        assert locate_user_cdb(tmp_path) is None

    def test_accepts_empty_compile_flags_txt_by_existence(self, tmp_path: Path) -> None:
        """``compile_flags.txt`` is raw flags for clangd -- empty is fine."""
        (tmp_path / "compile_flags.txt").write_text("")
        assert locate_user_cdb(tmp_path) == tmp_path


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

    def test_unknown_hint_is_dialect_neutral(self) -> None:
        # Why: a ``-std=c++20`` example in the UNKNOWN hint misleads users with
        # pure-C repos — clangd would parse ``.c`` files with C++ rules and emit
        # confusing diagnostics. Pin the wording so we never reintroduce a
        # dialect-specific flag in the fallback.
        hint = install_hint_for(BuildSystemKind.UNKNOWN)
        assert "-std=c++" not in hint
        assert "-std=c17" not in hint
        assert "-std=c11" not in hint

    def test_detection_result_with_existing_cdb_surfaces_path(self, tmp_path: Path) -> None:
        """When a CDB is already present, the hint should name its location
        rather than walk the user through generating one."""
        detection = DetectionResult(BuildSystemKind.UNKNOWN, tmp_path, existing_cdb=tmp_path)
        hint = install_hint_for(detection)
        assert "already detected" in hint
        assert str(tmp_path) in hint

    def test_detection_result_without_existing_cdb_falls_through_to_kind(self) -> None:
        detection = DetectionResult(BuildSystemKind.CMAKE, Path("/x"), existing_cdb=None)
        assert "CMAKE_EXPORT_COMPILE_COMMANDS" in install_hint_for(detection)


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
