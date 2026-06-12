"""Last-resort synthesized clangd flags for projects without an obtainable CDB.

Header-only repos (cccl, Eigen, Catch2) configure successfully yet export an
empty compilation database — there are no compilable TUs. clangd still parses
and cross-references such code fine given include roots, which is what a
``compile_flags.txt`` provides.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from static_analyzer.cdb.base import CDB_SKIP_DIRS, CDB_SUBDIR
from static_analyzer.cdb.cdb_io import clear_generated_compile_commands

logger = logging.getLogger(__name__)

_INCLUDE_DIR_NAMES = frozenset({"include", "inc"})
_MAX_DISCOVERY_DEPTH = 3
_MAX_INCLUDE_ROOTS = 50
_CUDA_SUFFIXES = (".cu", ".cuh")
# Build-output dirs whose ``include/`` subdirs are generated artifacts, not
# source include roots.
_DISCOVERY_SKIP_DIRS = CDB_SKIP_DIRS | {"build", "out", "cmake-build-debug", "cmake-build-release"}

# Stub out CUDA execution-space keywords so clangd's host parse survives
# ``__host__ __device__``-annotated headers (thrust/cub-style code).
_CUDA_KEYWORD_STUBS = (
    "-D__host__=",
    "-D__device__=",
    "-D__global__=",
    "-D__shared__=",
    "-D__constant__=",
    "-D__managed__=",
    "-D__forceinline__=inline",
    "-D__launch_bounds__(...)=",
)


def synthesize_fallback_flags(project_root: Path) -> Path | None:
    """Write a synthesized ``compile_flags.txt`` under ``.codeboarding/cdb``.

    Returns the directory to pass to clangd via ``--compile-commands-dir``,
    or ``None`` when the write fails. ``-I`` paths are absolute — clangd
    resolves relative flags against the flags file's own directory, which
    is not the project root.
    """
    root = project_root.resolve()
    flags = ["-std=c++20", f"-I{root}"]
    flags += [f"-I{d}" for d in _discover_include_roots(root)]
    if _has_cuda_sources(root):
        flags += _CUDA_KEYWORD_STUBS
    out_dir = project_root / CDB_SUBDIR
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
        # A stale invalid/empty generated compile_commands.json would shadow
        # the flags file — clangd's loader tries JSON first and an empty
        # ``[]`` loads "successfully" with zero commands.
        clear_generated_compile_commands(out_dir)
        (out_dir / "compile_flags.txt").write_text("\n".join(flags) + "\n", encoding="utf-8")
    except OSError as exc:
        logger.error("Could not write fallback compile_flags.txt under %s: %s", out_dir, exc)
        return None
    return out_dir


def _discover_include_roots(root: Path) -> list[Path]:
    """Collect ``include``/``inc`` directories within a shallow walk.

    Depth- and count-bounded so pathological repos can't make synthesis
    expensive; deterministic (sorted) so the flags file is stable across
    runs.
    """
    found: list[Path] = []
    stack: list[tuple[Path, int]] = [(root, 0)]
    while stack:
        d, depth = stack.pop()
        try:
            entries = sorted(e for e in d.iterdir() if e.is_dir() and not e.is_symlink())
        except OSError:
            continue
        for sub in entries:
            if sub.name.startswith(".") or sub.name in _DISCOVERY_SKIP_DIRS:
                continue
            if sub.name in _INCLUDE_DIR_NAMES:
                found.append(sub)
                if len(found) >= _MAX_INCLUDE_ROOTS:
                    return sorted(found)
            if depth + 1 < _MAX_DISCOVERY_DEPTH:
                stack.append((sub, depth + 1))
    return sorted(found)


def _has_cuda_sources(root: Path) -> bool:
    """True when any ``.cu``/``.cuh`` exists outside pruned dirs (early exit)."""
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if not d.startswith(".") and d not in _DISCOVERY_SKIP_DIRS]
        if any(f.endswith(_CUDA_SUFFIXES) for f in filenames):
            return True
    return False
