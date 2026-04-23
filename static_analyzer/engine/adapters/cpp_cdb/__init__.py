"""Compilation-database generation for the C++ adapter. See ``ensure_cdb``."""

from __future__ import annotations

import logging
import sys
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

logger = logging.getLogger(__name__)


def generator_for(kind: BuildSystemKind) -> CdbGenerator | None:
    """Map a detected build system to its generator, or ``None``.

    Why: CMake/Meson/Ninja return ``None`` because their CDB-export is a
    trivial one-liner we'd rather let the user run. On Windows Bear is
    unavailable (``LD_PRELOAD``), so Make/Autotools also return ``None``.
    """
    if sys.platform == "win32" and kind in (BuildSystemKind.MAKE, BuildSystemKind.AUTOTOOLS):
        return None
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
    "detect_build_system",
    "ensure_cdb",
    "generator_for",
    "install_hint_for",
    "locate_generated_cdb",
    "locate_user_cdb",
]
