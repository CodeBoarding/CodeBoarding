"""Detect a C/C++ project's build system from marker files.

Precedence is ordered so that a mixed repo (e.g. CMake that wraps a
vendored Autotools library) resolves to the outer build system — which is
the one whose ``compile_commands.json`` we'd actually want.
"""

from __future__ import annotations

import logging
from pathlib import Path

from static_analyzer.engine.adapters.cpp_cdb.base import BuildSystemKind

logger = logging.getLogger(__name__)


_CMAKE_MARKERS = ("CMakeLists.txt",)
_MESON_MARKERS = ("meson.build",)
_NINJA_MARKERS = ("build.ninja",)
_BAZEL_MARKERS = ("MODULE.bazel", "WORKSPACE", "WORKSPACE.bazel")
_AUTOTOOLS_MARKERS = ("configure.ac", "configure.in", "Makefile.am")
_MAKE_MARKERS = ("Makefile", "GNUmakefile", "makefile")


def detect_build_system(project_root: Path) -> BuildSystemKind:
    """Identify the build system at ``project_root`` by marker file.

    Returns :attr:`BuildSystemKind.UNKNOWN` when no recognised marker is
    found at the root (we never walk into subdirectories — a nested
    ``CMakeLists.txt`` two levels down belongs to a vendored dep, not the
    outer project).
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


def install_hint_for(kind: BuildSystemKind) -> str:
    """Return a one-paragraph user-facing hint for how to get a CDB.

    Used in ``CppAdapter``'s startup error when no CDB is present. Each
    branch names the concrete command the user would run — generic advice
    like "generate a compilation database" is worthless to a newbie.
    """
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
