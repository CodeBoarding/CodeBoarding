"""Tests for C/C++ build-system detection."""

from __future__ import annotations

import json
import logging
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
        """A valid pre-existing CDB lands in ``existing_cdb`` (not as a kind)."""
        (tmp_path / "compile_commands.json").write_text(
            json.dumps([{"directory": ".", "file": "x.cc", "command": "c++ -c x.cc"}])
        )
        result = detect_build_system(tmp_path)
        assert result.existing_cdb == tmp_path
        assert result.kind is BuildSystemKind.UNKNOWN

    def test_existing_compile_flags_txt_surfaces_via_existing_cdb(self, tmp_path: Path) -> None:
        (tmp_path / "compile_flags.txt").write_text("-std=c++20\n")
        result = detect_build_system(tmp_path)
        assert result.existing_cdb == tmp_path
        assert result.kind is BuildSystemKind.UNKNOWN

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
        assert detect_build_system(tmp_path).kind is BuildSystemKind.MAKE

    def test_gnumakefile_also_counts_as_make(self, tmp_path: Path) -> None:
        (tmp_path / "GNUmakefile").write_text("all:\n")
        assert detect_build_system(tmp_path).kind is BuildSystemKind.MAKE

    def test_existing_cdb_takes_precedence_over_cmake(self, tmp_path: Path) -> None:
        """A pre-existing ``compile_commands.json`` short-circuits via
        ``existing_cdb`` even when CMake markers are also present.
        """
        (tmp_path / "CMakeLists.txt").write_text("project(x)")
        (tmp_path / "compile_commands.json").write_text(
            json.dumps([{"directory": ".", "file": "x.cc", "command": "c++ -c x.cc"}])
        )
        result = detect_build_system(tmp_path)
        assert result.existing_cdb == tmp_path
        # kind still resolves so install_hint_for can fall back if generator is wanted.
        assert result.kind is BuildSystemKind.CMAKE

    def test_cmake_wins_over_make(self, tmp_path: Path) -> None:
        """CMake projects routinely ship a top-level Makefile wrapper; the
        CMake marker is more informative for the user-facing hint.
        """
        (tmp_path / "CMakeLists.txt").write_text("project(x)")
        (tmp_path / "Makefile").write_text("all:\n")
        assert detect_build_system(tmp_path).kind is BuildSystemKind.CMAKE

    def test_autotools_wins_over_make(self, tmp_path: Path) -> None:
        """Autotools generates a Makefile after ``./configure`` — the
        Autotools hint is correct, the Make hint would make the user wrap
        the build at the wrong layer.
        """
        (tmp_path / "configure.ac").write_text("AC_INIT")
        (tmp_path / "Makefile").write_text("all:\n")
        assert detect_build_system(tmp_path).kind is BuildSystemKind.AUTOTOOLS

    def test_makefile_in_src_subdir_detected(self, tmp_path: Path) -> None:
        """Stockfish-shape: bare root, Makefile in src/."""
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "Makefile").write_text("all:\n")
        result = detect_build_system(tmp_path)
        assert result.kind is BuildSystemKind.MAKE
        assert result.build_root == tmp_path / "src"

    def test_root_marker_wins_over_subdir(self, tmp_path: Path) -> None:
        (tmp_path / "CMakeLists.txt").write_text("project(x)")
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "Makefile").write_text("all:\n")
        result = detect_build_system(tmp_path)
        assert result.kind is BuildSystemKind.CMAKE
        assert result.build_root == tmp_path


class TestUserCdbProbeAlignment:
    """Bug H3: ``locate_user_cdb`` and ``detect_build_system`` must walk the
    same probe list so a ``src/compile_flags.txt`` is found everywhere.
    """

    def test_compile_flags_txt_in_src_found_by_user_cdb(self, tmp_path: Path) -> None:
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "compile_flags.txt").write_text("-std=c++20\n")
        assert locate_user_cdb(tmp_path) == tmp_path / "src"

    def test_compile_commands_json_in_source_found_by_user_cdb(self, tmp_path: Path) -> None:
        (tmp_path / "source").mkdir()
        (tmp_path / "source" / "compile_commands.json").write_text(
            json.dumps([{"directory": ".", "file": "x.cc", "command": "c++ -c x.cc"}])
        )
        assert locate_user_cdb(tmp_path) == tmp_path / "source"

    def test_compile_flags_in_src_surfaces_via_detection(self, tmp_path: Path) -> None:
        """``src/compile_flags.txt`` must appear as ``existing_cdb`` -- before
        the fix, detection set kind=COMPILE_FLAGS_TXT but ``locate_user_cdb``
        returned None, producing a "No recognised build system" hint.
        """
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "compile_flags.txt").write_text("-std=c++20\n")
        result = detect_build_system(tmp_path)
        assert result.existing_cdb == tmp_path / "src"

    def test_cmake_build_debug_probed(self, tmp_path: Path) -> None:
        (tmp_path / "cmake-build-debug").mkdir()
        (tmp_path / "cmake-build-debug" / "compile_flags.txt").write_text("-std=c++17\n")
        assert locate_user_cdb(tmp_path) == tmp_path / "cmake-build-debug"


class TestUserCdbValidation:
    """Bug M2: malformed user ``compile_commands.json`` must NOT be accepted
    silently — it would feed clangd an empty index.
    """

    def test_empty_user_cdb_is_skipped_with_warning(self, tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
        (tmp_path / "compile_commands.json").write_text("[]")
        with caplog.at_level(logging.WARNING, logger="static_analyzer.cdb.detect"):
            assert locate_user_cdb(tmp_path) is None
        assert any("Ignoring user compile_commands.json" in r.message for r in caplog.records)

    def test_malformed_json_user_cdb_is_skipped(self, tmp_path: Path) -> None:
        (tmp_path / "compile_commands.json").write_text("not json")
        assert locate_user_cdb(tmp_path) is None

    def test_invalid_entry_user_cdb_is_skipped(self, tmp_path: Path) -> None:
        (tmp_path / "compile_commands.json").write_text(json.dumps([{"directory": "."}]))
        assert locate_user_cdb(tmp_path) is None

    def test_valid_user_cdb_is_accepted(self, tmp_path: Path) -> None:
        (tmp_path / "compile_commands.json").write_text(
            json.dumps([{"directory": ".", "file": "x.cc", "command": "c++ -c x.cc"}])
        )
        assert locate_user_cdb(tmp_path) == tmp_path

    def test_compile_flags_txt_accepted_by_existence(self, tmp_path: Path) -> None:
        """``compile_flags.txt`` is just lines of flags — clangd reads it raw,
        so we never parse it; existence is enough.
        """
        (tmp_path / "compile_flags.txt").write_text("")
        assert locate_user_cdb(tmp_path) == tmp_path


class TestInstallHintFor:
    """Each hint must name a concrete command. Generic advice like "generate
    a CDB" is worthless to a newbie; we pin that as a test invariant.
    """

    def test_cmake_hint_mentions_env_var_and_export_flag(self) -> None:
        hint = install_hint_for(BuildSystemKind.CMAKE)
        assert "CODEBOARDING_CPP_GENERATE_CDB" in hint
        assert "CMAKE_EXPORT_COMPILE_COMMANDS" in hint

    def test_meson_hint_mentions_setup(self) -> None:
        hint = install_hint_for(BuildSystemKind.MESON)
        assert "meson setup" in hint
        assert "CODEBOARDING_CPP_GENERATE_CDB" in hint

    def test_ninja_hint_mentions_compdb(self) -> None:
        hint = install_hint_for(BuildSystemKind.NINJA)
        assert "ninja -t compdb" in hint
        assert "CODEBOARDING_CPP_GENERATE_CDB" in hint

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

    def test_detection_result_with_existing_cdb_returns_existing_hint(self, tmp_path: Path) -> None:
        """Bug H3: when a CDB already exists, the hint must say so instead of
        falling back to a "generate a CDB" message for the build system.
        """
        detection = DetectionResult(
            kind=BuildSystemKind.CMAKE,
            build_root=tmp_path,
            existing_cdb=tmp_path / "src",
        )
        hint = install_hint_for(detection)
        assert "already detected" in hint
        assert str(tmp_path / "src") in hint

    def test_detection_result_without_existing_cdb_falls_through_to_kind(self, tmp_path: Path) -> None:
        detection = DetectionResult(kind=BuildSystemKind.CMAKE, build_root=tmp_path, existing_cdb=None)
        assert install_hint_for(detection) == install_hint_for(BuildSystemKind.CMAKE)


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
