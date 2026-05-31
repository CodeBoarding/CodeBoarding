"""Compilation-database resolution and generation. See ``resolve_cdb``.

``resolve_cdb`` is the single facade consumed by ``CppAdapter`` --
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
from static_analyzer.cdb.config import is_generation_enabled
from static_analyzer.cdb.detect import (
    DetectionResult,
    detect_build_system,
    install_hint_for,
    locate_generated_cdb,
    locate_user_cdb,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CdbResolution:
    """Single resolution result shared by ``prepare_project`` and ``get_lsp_command``.

    ``cdb_dir`` is the directory containing the CDB clangd should walk
    from (the value of ``--compile-commands-dir`` for generated CDBs,
    or ``None`` when no usable CDB exists). ``detection`` is preserved
    for callers that want to surface build-system hints. ``error_hint``
    is populated when ``cdb_dir is None`` so the adapter can compose a
    user-facing message without re-running detection.

    ``needs_compile_commands_dir`` is the authoritative signal for
    ``--compile-commands-dir``: ``True`` when ``cdb_dir`` is anything but
    the project root (subdir user CDB, generated CDB), ``False`` when the
    CDB sits at the root (clangd's walk-up finds it) or when ``cdb_dir``
    is ``None``. Decouples adapters from path-identity comparisons.
    """

    cdb_dir: Path | None
    detection: DetectionResult
    error_hint: str | None = None
    needs_compile_commands_dir: bool = False


def generator_for(kind: BuildSystemKind) -> CdbGenerator | None:
    """Map a detected build system to its generator, or ``None``.

    Why: also serves as a test-injection point -- ``ensure_cdb`` resolves
    its concrete generator through this seam so tests can swap it.
    CMake/Meson/Ninja return ``None`` because their CDB-export is a
    one-liner we'd rather let the user run; Bear needs ``LD_PRELOAD`` so
    Make/Autotools also return ``None`` on Windows.
    """
    if sys.platform == "win32" and kind in (BuildSystemKind.MAKE, BuildSystemKind.AUTOTOOLS):
        return None
    if kind in (BuildSystemKind.MAKE, BuildSystemKind.AUTOTOOLS):
        return BearGenerator(kind)
    if kind is BuildSystemKind.BAZEL:
        return BazelAqueryGenerator()
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

    The generated CDB always lands at ``<project_root>/.codeboarding/cdb/``
    regardless of where the build files live (Stockfish-shape support).

    Why ``detection`` is optional: ``resolve_cdb`` already runs detection
    and passes the result through to avoid the ``_PROBE_SUBDIRS`` walk
    happening twice per analysis. Legacy/direct callers can omit it.
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
            build_cwd = detection.build_root
        else:
            # UNKNOWN is a sentinel, not a buildable kind. Fall back to detection
            # so a stray ``unknown``/``compile_flags_txt`` doesn't dispatch nothing.
            if kind is BuildSystemKind.UNKNOWN:
                logger.warning(
                    "Ignoring CODEBOARDING_CPP_BUILD_SYSTEM=%r (not a buildable system); " "falling back to detection.",
                    override,
                )
                kind = detection.kind
                build_cwd = detection.build_root
            elif kind is detection.kind:
                build_cwd = detection.build_root
            else:
                # User picked a different kind than detection -- aim at the
                # project root rather than detection.build_root.
                build_cwd = project_root
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
      * User CDB at root -> ``project_root`` (clangd's walk-up finds it,
        no ``--compile-commands-dir`` needed).
      * User CDB in a subdir (``src/``, ``build/``, ...) -> the subdir
        itself, so ``--compile-commands-dir`` is passed and sibling-dir
        sources (``lib/``, ``tests/``) are also indexed against it.
      * Generated CDB found -> ``<project_root>/.codeboarding/cdb``
        (the dir to pass to ``--compile-commands-dir``).
      * Nothing usable -> ``None`` and ``error_hint`` is populated.
    """
    detection = detect_build_system(project_root)
    if detection.existing_cdb is not None:
        # Why: ``existing_cdb`` is the *directory* containing the user CDB
        # (see ``locate_user_cdb``). Preserve it so files outside that dir
        # still resolve via ``--compile-commands-dir`` -- clangd's walk-up
        # alone would skip them.
        needs_dir = detection.existing_cdb != project_root
        return CdbResolution(
            cdb_dir=detection.existing_cdb,
            detection=detection,
            needs_compile_commands_dir=needs_dir,
        )

    ensure_cdb(project_root, detection)
    if locate_generated_cdb(project_root) is not None:
        # Generated CDB lives at ``<project_root>/.codeboarding/cdb/`` —
        # clangd's walk-up search from source files never visits it.
        return CdbResolution(
            cdb_dir=project_root / CDB_SUBDIR,
            detection=detection,
            needs_compile_commands_dir=True,
        )

    return CdbResolution(cdb_dir=None, detection=detection, error_hint=install_hint_for(detection))


__all__ = [
    "BazelAqueryGenerator",
    "BearGenerator",
    "BuildSystemKind",
    "CdbGenerator",
    "CdbResolution",
    "DetectionResult",
    "detect_build_system",
    "ensure_cdb",
    "generator_for",
    "install_hint_for",
    "locate_generated_cdb",
    "locate_user_cdb",
    "resolve_cdb",
]
