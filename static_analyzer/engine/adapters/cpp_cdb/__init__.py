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

import logging
from pathlib import Path

from static_analyzer.engine.adapters.cpp_cdb.base import BuildSystemKind, CdbGenerator
from static_analyzer.engine.adapters.cpp_cdb.bazel_generator import BazelAqueryGenerator
from static_analyzer.engine.adapters.cpp_cdb.bear_generator import BearGenerator
from static_analyzer.engine.adapters.cpp_cdb.config import is_generation_enabled
from static_analyzer.engine.adapters.cpp_cdb.detect import (
    detect_build_system,
    install_hint_for,
    locate_generated_cdb,
    locate_user_cdb,
)
from static_analyzer.engine.adapters.cpp_cdb.fingerprint import (
    compute_fingerprint,
    read_cached_fingerprint,
    write_cached_fingerprint,
)

logger = logging.getLogger(__name__)


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


def ensure_cdb(project_root: Path) -> Path | None:
    """Return a usable CDB path for clangd, or ``None``.

    Resolution order:
      1. User-owned CDB (root, build/, cmake-build-*) — never clobber.
      2. Opt-in disabled → ``None``.
      3. Detect build system → ``generator_for`` → ``generate``.
         Generator ``RuntimeError``s are logged and surface as ``None``.
    """
    user_cdb = locate_user_cdb(project_root)
    if user_cdb is not None:
        return user_cdb
    if not is_generation_enabled():
        return None
    kind = detect_build_system(project_root)
    generator = generator_for(kind)
    if generator is None:
        logger.info("Auto-generation not supported for build system %s", kind)
        return None
    try:
        cdb_path = generator.generate(project_root)
        logger.info("Generated %s via %s", cdb_path, generator.kind)
        return cdb_path
    except RuntimeError as exc:
        logger.error("CDB generation failed (%s): %s", generator.kind, exc)
        return None


__all__ = [
    "BazelAqueryGenerator",
    "BearGenerator",
    "BuildSystemKind",
    "CdbGenerator",
    "compute_fingerprint",
    "detect_build_system",
    "ensure_cdb",
    "generator_for",
    "install_hint_for",
    "locate_generated_cdb",
    "locate_user_cdb",
    "read_cached_fingerprint",
    "write_cached_fingerprint",
]
