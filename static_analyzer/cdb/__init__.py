"""Compilation-database resolution and generation. See ``resolve_cdb``.

``resolve_cdb`` is the single facade consumed by ``CppAdapter`` —
``prepare_project`` and ``get_lsp_command`` both go through it so detection
and generation only run once per project.
"""

from __future__ import annotations

import logging
import os
import sys
from dataclasses import dataclass
from pathlib import Path

from static_analyzer.cdb.base import CDB_SUBDIR, BuildSystemKind, CdbGenerator
from static_analyzer.cdb.bazel_generator import BazelAqueryGenerator
from static_analyzer.cdb.bear_generator import BearGenerator
from static_analyzer.cdb.cmake_meson_ninja_generator import (
    CMakeGenerator,
    MesonGenerator,
    NinjaGenerator,
)
from static_analyzer.cdb.config import is_generation_enabled
from static_analyzer.cdb.detect import (
    DetectionResult,
    detect_build_system,
    install_hint_for,
    locate_generated_cdb,
    locate_user_cdb,
)
from static_analyzer.cdb.fallback_flags import synthesize_fallback_flags

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CdbResolution:
    """Single resolution result shared by ``prepare_project`` and ``get_lsp_command``.

    ``cdb_dir`` is the directory containing the CDB clangd should walk
    from (the value of ``--compile-commands-dir`` for generated CDBs,
    or ``None`` when no usable CDB exists). ``detection`` is preserved
    for callers that want to surface build-system hints. ``error_hint``
    is populated when ``cdb_dir is None`` so the adapter can compose a
    user-facing message without re-running detection. ``is_fallback``
    marks a synthesized ``compile_flags.txt`` (degraded fidelity).
    """

    cdb_dir: Path | None
    detection: DetectionResult
    error_hint: str | None = None
    is_fallback: bool = False


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


def ensure_cdb(project_root: Path, detection: DetectionResult | None = None) -> Path | None:
    """Return a usable CDB path for clangd, or ``None``.

    Resolution order:
      1. User-owned CDB anywhere along the probe -- never clobber.
      2. Opt-in disabled -> ``None``.
      3. ``CODEBOARDING_CPP_BUILD_SYSTEM`` override (buildable kinds
         only), else detect.
      4. ``generator_for`` -> ``generate``. Generator ``RuntimeError``s
         are logged and surface as ``None``.

    ``detection`` lets ``resolve_cdb`` share its result instead of
    re-probing (which would also duplicate user-CDB warnings).
    """
    if detection is None:
        detection = detect_build_system(project_root)
    if detection.existing_cdb is not None:
        return detection.existing_cdb
    if not is_generation_enabled():
        return None
    override = os.environ.get("CODEBOARDING_CPP_BUILD_SYSTEM", "").strip().lower()
    if override:
        try:
            kind = BuildSystemKind(override)
        except ValueError:
            logger.warning("Ignoring CODEBOARDING_CPP_BUILD_SYSTEM=%r (unknown kind)", override)
            kind = detection.kind
        else:
            # ``unknown`` is a sentinel, not a buildable kind. Falling back to
            # detection is friendlier than silently dispatching nothing.
            if kind is BuildSystemKind.UNKNOWN:
                logger.warning(
                    "Ignoring CODEBOARDING_CPP_BUILD_SYSTEM=%r (not a buildable system); falling back to detection.",
                    override,
                )
                kind = detection.kind
        # When the override picks a different kind than detection, point the
        # build cwd at the project root rather than detection.build_root.
        build_cwd = detection.build_root if kind is detection.kind else project_root
    else:
        kind, build_cwd = detection.kind, detection.build_root
    generator = generator_for(kind)
    if generator is None:
        logger.info("Auto-generation not supported for build system %s", kind)
        return None
    try:
        cdb_path = generator.generate(project_root, build_cwd)
        logger.info("Generated %s via %s", cdb_path, generator.kind)
        return cdb_path
    except RuntimeError as exc:
        logger.error("CDB generation failed (%s): %s", generator.kind, exc)
        return None


def resolve_cdb(project_root: Path) -> CdbResolution:
    """Return a single resolution result for ``CppAdapter`` consumption.

    Chains detection -> ``ensure_cdb`` so both ``prepare_project`` and
    ``get_lsp_command`` share one outcome and avoid re-running detection.

    ``cdb_dir`` semantics:
      * User CDB found -> ``project_root`` (clangd's walk-up finds it,
        no ``--compile-commands-dir`` needed).
      * Generated CDB found -> ``<project_root>/.codeboarding/cdb``
        (the dir to pass to ``--compile-commands-dir``).
      * Neither -> synthesized fallback ``compile_flags.txt`` in the same
        dir (header-only repos export empty CDBs; degraded beats refusal).
      * Synthesis write failure -> ``None`` and ``error_hint`` is populated.
    """
    detection = detect_build_system(project_root)
    if detection.existing_cdb is not None:
        # User CDB wins; clangd walks up from sources so cdb_dir is project_root.
        return CdbResolution(cdb_dir=project_root, detection=detection)

    ensure_cdb(project_root, detection)
    if locate_generated_cdb(project_root) is not None:
        return CdbResolution(cdb_dir=project_root / CDB_SUBDIR, detection=detection)

    fallback_dir = synthesize_fallback_flags(project_root)
    if fallback_dir is not None:
        logger.warning(
            "No usable compilation database for %s; running clangd with synthesized fallback flags "
            "(%s). Cross-file fidelity may be reduced. %s",
            project_root,
            fallback_dir / "compile_flags.txt",
            install_hint_for(detection),
        )
        return CdbResolution(cdb_dir=fallback_dir, detection=detection, is_fallback=True)

    return CdbResolution(cdb_dir=None, detection=detection, error_hint=install_hint_for(detection))


__all__ = [
    "BazelAqueryGenerator",
    "BearGenerator",
    "BuildSystemKind",
    "CMakeGenerator",
    "CdbGenerator",
    "CdbResolution",
    "DetectionResult",
    "MesonGenerator",
    "NinjaGenerator",
    "detect_build_system",
    "ensure_cdb",
    "generator_for",
    "install_hint_for",
    "locate_generated_cdb",
    "locate_user_cdb",
    "resolve_cdb",
    "synthesize_fallback_flags",
]
