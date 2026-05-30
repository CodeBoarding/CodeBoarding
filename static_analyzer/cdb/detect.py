"""Detect a C/C++ project's build system from marker files."""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import NamedTuple

from static_analyzer.cdb.base import CDB_SUBDIR, BuildSystemKind
from static_analyzer.cdb.cdb_io import is_valid_compile_commands

logger = logging.getLogger(__name__)


_CMAKE_MARKERS = ("CMakeLists.txt",)
_MESON_MARKERS = ("meson.build",)
_NINJA_MARKERS = ("build.ninja",)
_BAZEL_MARKERS = ("MODULE.bazel", "WORKSPACE", "WORKSPACE.bazel")
_AUTOTOOLS_MARKERS = ("configure.ac", "configure.in", "Makefile.am")
_MAKE_MARKERS = ("Makefile", "GNUmakefile", "makefile")

# Probed in order; root wins ties so a `Makefile` in src/ only fires when root
# is bare. Stockfish-shaped repos (Makefile in src/) need this. The same list
# is used by `locate_user_cdb` so detection and user-CDB lookup never diverge.
_PROBE_SUBDIRS: tuple[str, ...] = (
    "",
    "src",
    "source",
    "build",
    "build/Debug",
    "build/Release",
    "cmake-build-debug",
    "cmake-build-release",
)


class DetectionResult(NamedTuple):
    """Outcome of probing a project root for a C/C++ build system.

    ``existing_cdb`` short-circuits generation when set: it's the directory
    containing a user-owned ``compile_commands.json`` or ``compile_flags.txt``.
    ``kind`` is the buildable system (UNKNOWN if none) and ``build_root`` is
    its cwd for generators.
    """

    kind: BuildSystemKind
    build_root: Path
    existing_cdb: Path | None = None


def detect_build_system(project_root: Path) -> DetectionResult:
    """Identify the build system at ``project_root``.

    Probes the root first, then the subdirs in :data:`_PROBE_SUBDIRS`. A
    pre-existing CDB found anywhere along the probe wins and is returned
    via ``existing_cdb`` — the caller skips generation.
    """
    if not project_root.is_dir():
        return DetectionResult(BuildSystemKind.UNKNOWN, project_root, None)

    existing_cdb = locate_user_cdb(project_root)
    build_kind = BuildSystemKind.UNKNOWN
    build_root = project_root
    for sub in _PROBE_SUBDIRS:
        d = project_root / sub if sub else project_root
        if not d.is_dir():
            continue
        kind = _detect_build_kind_at(d)
        if kind is not BuildSystemKind.UNKNOWN:
            build_kind, build_root = kind, d
            break
    return DetectionResult(build_kind, build_root, existing_cdb)


def _detect_build_kind_at(d: Path) -> BuildSystemKind:
    """Identify the buildable system rooted at ``d``, or UNKNOWN."""
    if _any_exists(d, _CMAKE_MARKERS):
        return BuildSystemKind.CMAKE
    if _any_exists(d, _MESON_MARKERS):
        return BuildSystemKind.MESON
    if _any_exists(d, _BAZEL_MARKERS):
        return BuildSystemKind.BAZEL
    if _any_exists(d, _AUTOTOOLS_MARKERS):
        return BuildSystemKind.AUTOTOOLS
    if _any_exists(d, _NINJA_MARKERS):
        return BuildSystemKind.NINJA
    if _any_exists(d, _MAKE_MARKERS):
        return BuildSystemKind.MAKE
    return BuildSystemKind.UNKNOWN


def _any_exists(root: Path, names: tuple[str, ...]) -> bool:
    return any((root / name).is_file() for name in names)


def locate_user_cdb(project_root: Path) -> Path | None:
    """Return the first user-owned CDB directory found, or ``None``.

    Why: a hit must short-circuit generation so we never rebuild on top
    of a CDB the user emitted. Walks the same probe list as
    :func:`detect_build_system` so a ``src/compile_flags.txt`` is found.
    Excludes ``.codeboarding/cdb`` (our output).

    Invalid ``compile_commands.json`` files are skipped with a warning so
    a malformed user CDB falls through to generation rather than silently
    producing empty clangd analysis. ``compile_flags.txt`` is accepted on
    existence (clangd reads it as raw flags).
    """
    for sub in _PROBE_SUBDIRS:
        root = project_root / sub if sub else project_root
        if not root.is_dir():
            continue
        if (root / "compile_flags.txt").is_file():
            return root
        ccj = root / "compile_commands.json"
        if ccj.is_file():
            if is_valid_compile_commands(ccj):
                return root
            logger.warning(
                "Ignoring user compile_commands.json at %s (invalid or empty); "
                "falling through to detection/generation.",
                ccj,
            )
    return None


def locate_generated_cdb(project_root: Path) -> Path | None:
    """Return the generated CDB path when it exists and passes validation."""
    cdb = project_root / CDB_SUBDIR / "compile_commands.json"
    return cdb if is_valid_compile_commands(cdb) else None


def install_hint_for(detection: DetectionResult | BuildSystemKind) -> str:
    """Return a user-facing hint for how to get a CDB.

    Accepts either a full :class:`DetectionResult` (preferred — surfaces
    the existing-CDB hint when one is present) or a bare
    :class:`BuildSystemKind` for legacy call sites.
    """
    if isinstance(detection, DetectionResult):
        if detection.existing_cdb is not None:
            return (
                f"A compile database was already detected at {detection.existing_cdb}. "
                "If clangd still can't find it, ensure the project root passed to "
                "CodeBoarding contains it (or set CODEBOARDING_CPP_GENERATE_CDB=1)."
            )
        kind = detection.kind
    else:
        kind = detection
    if kind is BuildSystemKind.CMAKE:
        return (
            "Detected CMake (CMakeLists.txt). Set CODEBOARDING_CPP_GENERATE_CDB=1 to let "
            "CodeBoarding run 'cmake -DCMAKE_EXPORT_COMPILE_COMMANDS=ON' on your behalf."
        )
    if kind is BuildSystemKind.MESON:
        return (
            "Detected Meson (meson.build). Set CODEBOARDING_CPP_GENERATE_CDB=1 to let "
            "CodeBoarding run 'meson setup' on your behalf."
        )
    if kind is BuildSystemKind.NINJA:
        return (
            "Detected Ninja (build.ninja). Set CODEBOARDING_CPP_GENERATE_CDB=1 to let "
            "CodeBoarding run 'ninja -t compdb' on your behalf."
        )
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
        "compile_flags.txt with e.g. '-std=c++20\\n-Iinclude' - clangd reads "
        "it verbatim as flags for every .cpp/.h under the project."
    )
