"""Compilation-database generation helpers for the C++ adapter.

The adapter itself only needs clangd to find a ``compile_commands.json`` or
``compile_flags.txt``. Everything under this package exists to bridge the gap
for projects whose build system doesn't emit one natively — detection
(``detect.py``), fingerprint-based caching (``fingerprint.py``), and the
per-build-system generators (``bear_generator.py``, ``bazel_generator.py``).

Generation never runs implicitly: the dispatcher consults an explicit opt-in
(env var / config key) because invoking ``make`` or ``bazel build`` is
slow and has real blast radius.
"""

from __future__ import annotations

from static_analyzer.engine.adapters.cpp_cdb.base import BuildSystemKind, CdbGenerator
from static_analyzer.engine.adapters.cpp_cdb.bazel_generator import BazelAqueryGenerator
from static_analyzer.engine.adapters.cpp_cdb.bear_generator import BearGenerator
from static_analyzer.engine.adapters.cpp_cdb.detect import detect_build_system, install_hint_for
from static_analyzer.engine.adapters.cpp_cdb.fingerprint import (
    compute_fingerprint,
    read_cached_fingerprint,
    write_cached_fingerprint,
)


def generator_for(kind: BuildSystemKind) -> CdbGenerator | None:
    """Map a detected build system to its generator, or ``None`` if we don't
    auto-drive it.

    CMake, Meson, and Ninja are deliberately ``None`` — their CDB-export
    commands are trivial one-liners and auto-running them would configure
    the user's repo without asking. The error hint already tells the user
    exactly what to type.
    """
    if kind in (BuildSystemKind.MAKE, BuildSystemKind.AUTOTOOLS):
        return BearGenerator(kind)
    if kind is BuildSystemKind.BAZEL:
        return BazelAqueryGenerator()
    return None


__all__ = [
    "BazelAqueryGenerator",
    "BearGenerator",
    "BuildSystemKind",
    "CdbGenerator",
    "compute_fingerprint",
    "detect_build_system",
    "generator_for",
    "install_hint_for",
    "read_cached_fingerprint",
    "write_cached_fingerprint",
]
