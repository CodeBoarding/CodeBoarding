"""Native CDB generators for build systems that emit ``compile_commands.json`` themselves.

CMake, Meson, and Ninja each have a one-liner that produces the CDB without
needing Bear or any LD_PRELOAD trick. We run that one-liner into a hidden
out-of-tree dir and hand the result to the template method.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from static_analyzer.cdb.base import (
    CDB_SUBDIR,
    CPP_SOURCE_EXTENSIONS,
    BuildSystemKind,
    CdbGenerator,
    run_build_step,
)
from static_analyzer.cdb.cdb_io import read_compile_commands
from static_analyzer.cdb.fingerprint import collect_project_sources


class CMakeGenerator(CdbGenerator):
    """Configure CMake into a hidden build dir with ``CMAKE_EXPORT_COMPILE_COMMANDS=ON``."""

    @property
    def kind(self) -> BuildSystemKind:
        return BuildSystemKind.CMAKE

    def _fingerprint_inputs(self, project_root: Path) -> list[Path]:
        out = list(project_root.rglob("CMakeLists.txt"))
        out.extend(project_root.rglob("*.cmake"))
        out.extend(collect_project_sources(project_root, CPP_SOURCE_EXTENSIONS))
        return out

    def _build_entries(self, project_root: Path) -> list[dict]:
        build_dir = project_root / CDB_SUBDIR / "_cmake-build"
        build_dir.mkdir(parents=True, exist_ok=True)
        # Ninja makes the configure step ~3x faster than Make and is the canonical
        # CMake-tooling generator; Unix Makefiles is the universal fallback.
        cmake_generator = "Ninja" if shutil.which("ninja") else "Unix Makefiles"
        run_build_step(
            [
                "cmake",
                "-S",
                str(project_root),
                "-B",
                str(build_dir),
                "-G",
                cmake_generator,
                "-DCMAKE_EXPORT_COMPILE_COMMANDS=ON",
                "-DCMAKE_BUILD_TYPE=Release",
            ],
            cwd=project_root,
            step="cmake configure",
        )
        return read_compile_commands(build_dir / "compile_commands.json")


class MesonGenerator(CdbGenerator):
    """``meson setup`` writes ``compile_commands.json`` as a side effect."""

    @property
    def kind(self) -> BuildSystemKind:
        return BuildSystemKind.MESON

    def _fingerprint_inputs(self, project_root: Path) -> list[Path]:
        out = list(project_root.rglob("meson.build"))
        out.extend(project_root.rglob("meson.options"))
        out.extend(project_root.rglob("meson_options.txt"))
        out.extend(collect_project_sources(project_root, CPP_SOURCE_EXTENSIONS))
        return out

    def _build_entries(self, project_root: Path) -> list[dict]:
        build_dir = project_root / CDB_SUBDIR / "_meson-build"
        run_build_step(
            ["meson", "setup", str(build_dir), str(project_root), "--reconfigure"],
            cwd=project_root,
            step="meson setup",
        )
        return read_compile_commands(build_dir / "compile_commands.json")


class NinjaGenerator(CdbGenerator):
    """``ninja -t compdb`` against an existing ``build.ninja``."""

    @property
    def kind(self) -> BuildSystemKind:
        return BuildSystemKind.NINJA

    def _fingerprint_inputs(self, project_root: Path) -> list[Path]:
        return [project_root / "build.ninja", *collect_project_sources(project_root, CPP_SOURCE_EXTENSIONS)]

    def _build_entries(self, project_root: Path) -> list[dict]:
        result = run_build_step(["ninja", "-t", "compdb"], cwd=project_root, step="ninja -t compdb")
        try:
            entries = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"ninja -t compdb produced invalid JSON: {exc}") from exc
        if not isinstance(entries, list):
            raise RuntimeError("ninja -t compdb did not return a JSON array")
        return entries
