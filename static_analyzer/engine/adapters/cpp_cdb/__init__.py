"""Compilation-database generation for the C++ adapter. See ``ensure_cdb``."""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

from static_analyzer.engine.adapters.cpp_cdb.base import BuildSystemKind, CdbGenerator
from static_analyzer.engine.adapters.cpp_cdb.bazel_generator import BazelAqueryGenerator
from static_analyzer.engine.adapters.cpp_cdb.bear_generator import BearGenerator
from static_analyzer.engine.adapters.cpp_cdb.cmake_meson_ninja_generator import (
    CMakeGenerator,
    MesonGenerator,
    NinjaGenerator,
)
from static_analyzer.engine.adapters.cpp_cdb.config import is_generation_enabled
from static_analyzer.engine.adapters.cpp_cdb.detect import (
    detect_build_system,
    install_hint_for,
    locate_generated_cdb,
    locate_user_cdb,
)

logger = logging.getLogger(__name__)


def generator_for(kind: BuildSystemKind) -> CdbGenerator | None:
    """Resolve a build-system kind to its generator, or ``None``.

    Also the test-injection point — ``ensure_cdb`` goes through this seam
    so tests can swap the concrete generator. Returns ``None`` on Windows
    for Make/Autotools (Bear needs LD_PRELOAD).
    """
    if sys.platform == "win32" and kind in (BuildSystemKind.MAKE, BuildSystemKind.AUTOTOOLS):
        return None
    if kind in (BuildSystemKind.MAKE, BuildSystemKind.AUTOTOOLS):
        return BearGenerator(kind)
    if kind is BuildSystemKind.BAZEL:
        return BazelAqueryGenerator()
    if kind is BuildSystemKind.CMAKE:
        return CMakeGenerator()
    if kind is BuildSystemKind.MESON:
        return MesonGenerator()
    if kind is BuildSystemKind.NINJA:
        return NinjaGenerator()
    return None


def ensure_cdb(project_root: Path) -> Path | None:
    """Return a usable CDB path for clangd, or ``None``.

    Resolution order:
      1. User-owned CDB (root, build/, cmake-build-*) -- never clobber.
      2. Opt-in disabled -> ``None``.
      3. ``CODEBOARDING_CPP_BUILD_SYSTEM`` override, else detect.
      4. ``generator_for`` -> ``generate``. Generator ``RuntimeError``s
         are logged and surface as ``None``.
    """
    user_cdb = locate_user_cdb(project_root)
    if user_cdb is not None:
        return user_cdb
    if not is_generation_enabled():
        return None
    override = os.environ.get("CODEBOARDING_CPP_BUILD_SYSTEM", "").strip().lower()
    if override:
        try:
            kind, build_root = BuildSystemKind(override), project_root
        except ValueError:
            logger.warning("Ignoring CODEBOARDING_CPP_BUILD_SYSTEM=%r (unknown kind)", override)
            kind, build_root = detect_build_system(project_root)
    else:
        kind, build_root = detect_build_system(project_root)
    generator = generator_for(kind)
    if generator is None:
        logger.info("Auto-generation not supported for build system %s", kind)
        return None
    try:
        cdb_path = generator.generate(build_root)
        logger.info("Generated %s via %s", cdb_path, generator.kind)
        return cdb_path
    except RuntimeError as exc:
        logger.error("CDB generation failed (%s): %s", generator.kind, exc)
        return None


__all__ = [
    "BazelAqueryGenerator",
    "BearGenerator",
    "BuildSystemKind",
    "CMakeGenerator",
    "CdbGenerator",
    "MesonGenerator",
    "NinjaGenerator",
    "detect_build_system",
    "ensure_cdb",
    "generator_for",
    "install_hint_for",
    "locate_generated_cdb",
    "locate_user_cdb",
]
