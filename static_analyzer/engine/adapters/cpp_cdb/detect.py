"""Detect a C/C++ project's build system from marker files."""

from __future__ import annotations

import logging
import sys
from pathlib import Path

from static_analyzer.engine.adapters.cpp_cdb.base import CDB_SUBDIR, BuildSystemKind
from static_analyzer.engine.adapters.cpp_cdb.cdb_io import is_valid_compile_commands

logger = logging.getLogger(__name__)


_CMAKE_MARKERS = ("CMakeLists.txt",)
_MESON_MARKERS = ("meson.build",)
_NINJA_MARKERS = ("build.ninja",)
_BAZEL_MARKERS = ("MODULE.bazel", "WORKSPACE", "WORKSPACE.bazel")
_AUTOTOOLS_MARKERS = ("configure.ac", "configure.in", "Makefile.am")
_MAKE_MARKERS = ("Makefile", "GNUmakefile", "makefile")


def detect_build_system(project_root: Path) -> BuildSystemKind:
    """Identify the build system at ``project_root`` by marker file.

    Why: root-only; a nested ``CMakeLists.txt`` belongs to a vendored dep,
    not the outer project.
    """
    if not project_root.is_dir():
        return BuildSystemKind.UNKNOWN

    # Pre-generated CDB beats every marker — if it already exists we don't
    # need to know how it was built.
    if (project_root / "compile_commands.json").is_file():
        return BuildSystemKind.COMPILE_COMMANDS_JSON
    if (project_root / "compile_flags.txt").is_file():
        return BuildSystemKind.COMPILE_FLAGS_TXT

    if _any_exists(project_root, _CMAKE_MARKERS):
        return BuildSystemKind.CMAKE
    if _any_exists(project_root, _MESON_MARKERS):
        return BuildSystemKind.MESON
    if _any_exists(project_root, _BAZEL_MARKERS):
        return BuildSystemKind.BAZEL
    if _any_exists(project_root, _AUTOTOOLS_MARKERS):
        return BuildSystemKind.AUTOTOOLS
    if _any_exists(project_root, _NINJA_MARKERS):
        return BuildSystemKind.NINJA
    if _any_exists(project_root, _MAKE_MARKERS):
        return BuildSystemKind.MAKE
    return BuildSystemKind.UNKNOWN


def _any_exists(root: Path, names: tuple[str, ...]) -> bool:
    return any((root / name).is_file() for name in names)


_USER_CDB_SUBDIRS = (
    Path(""),
    Path("build"),
    Path("build") / "Debug",
    Path("build") / "Release",
    Path("cmake-build-debug"),
    Path("cmake-build-release"),
)


def locate_user_cdb(project_root: Path) -> Path | None:
    """Return the first user-owned CDB directory found, or ``None``.

    Why: a hit must short-circuit generation so we never rebuild on top
    of a CDB the user emitted. Excludes ``.codeboarding/cdb`` (our output).
    """
    for rel in _USER_CDB_SUBDIRS:
        root = project_root / rel
        if (root / "compile_flags.txt").is_file():
            return root
        if (root / "compile_commands.json").is_file():
            return root
    return None


def locate_generated_cdb(project_root: Path) -> Path | None:
    """Return the generated CDB path when it exists and passes validation."""
    cdb = project_root / CDB_SUBDIR / "compile_commands.json"
    return cdb if is_valid_compile_commands(cdb) else None


def install_hint_for(kind: BuildSystemKind) -> str:
    """Return a user-facing hint for how to get a CDB for ``kind``."""
    if kind is BuildSystemKind.CMAKE:
        return (
            "Detected CMake (CMakeLists.txt). Regenerate with "
            "'cmake -S . -B build -DCMAKE_EXPORT_COMPILE_COMMANDS=ON' "
            "then re-run the analysis."
        )
    if kind is BuildSystemKind.MESON:
        return (
            "Detected Meson (meson.build). Run 'meson setup build' — "
            "the CDB is emitted to build/compile_commands.json automatically."
        )
    if kind is BuildSystemKind.NINJA:
        return "Detected Ninja (build.ninja). Run 'ninja -t compdb > compile_commands.json' " "at the project root."
    if kind is BuildSystemKind.BAZEL:
        return (
            "Detected Bazel (MODULE.bazel / WORKSPACE). Set "
            "CODEBOARDING_CPP_GENERATE_CDB=1 to let CodeBoarding run 'bazel aquery' "
            "on your behalf, or generate manually with "
            "hedronvision/bazel-compile-commands-extractor."
        )
    if sys.platform == "win32" and kind in (BuildSystemKind.MAKE, BuildSystemKind.AUTOTOOLS):
        return (
            "Bear (the Make/Autotools CDB generator CodeBoarding uses on Unix) "
            "is not available on Windows. Use CMake with "
            "-DCMAKE_EXPORT_COMPILE_COMMANDS=ON, or hand-author a compile_flags.txt."
        )
    if kind is BuildSystemKind.MAKE:
        return (
            "Detected Make (Makefile). Set CODEBOARDING_CPP_GENERATE_CDB=1 to let "
            "CodeBoarding wrap the build with 'bear' on your behalf, or run "
            "'bear -- make clean all' manually and commit compile_commands.json."
        )
    if kind is BuildSystemKind.AUTOTOOLS:
        return (
            "Detected Autotools (configure.ac / Makefile.am). Set "
            "CODEBOARDING_CPP_GENERATE_CDB=1 to let CodeBoarding run "
            "'./configure && bear -- make' on your behalf, or do so manually."
        )
    return (
        "No recognised build system at the project root. The simplest fix is a "
        "compile_flags.txt with e.g. '-std=c++20\\n-Iinclude' — clangd reads "
        "it verbatim as flags for every .cpp/.h under the project."
    )
