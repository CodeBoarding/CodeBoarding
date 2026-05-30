"""Compilation-database generator backed by Bear for Make and Autotools.

Bear intercepts compiler invocations and writes them to
``compile_commands.json``. Bear 3.x only (2.x CLI is incompatible).
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
from pathlib import Path

from static_analyzer.cdb import config
from static_analyzer.cdb.base import (
    CDB_SUBDIR,
    CPP_SOURCE_EXTENSIONS,
    BuildSystemKind,
    CdbGenerator,
    run_build_step,
)
from static_analyzer.cdb.cdb_io import (
    read_compile_commands,
    temp_compile_commands_path,
)
from static_analyzer.cdb.fingerprint import collect_project_sources

logger = logging.getLogger(__name__)

_BEAR_VERSION_RE = re.compile(r"bear\s+(\d+)", re.IGNORECASE)
_MIN_BEAR_MAJOR = 3


class BearGenerator(CdbGenerator):
    """Drives Bear over Make or Autotools to produce a ``compile_commands.json``."""

    def __init__(self, kind: BuildSystemKind) -> None:
        if kind not in (BuildSystemKind.MAKE, BuildSystemKind.AUTOTOOLS):
            raise ValueError(f"BearGenerator cannot handle {kind}")
        self._kind = kind

    @property
    def kind(self) -> BuildSystemKind:
        return self._kind

    def _build_entries(self, project_root: Path) -> list[dict]:
        cdb_dir = project_root / CDB_SUBDIR
        self._require_bear()
        self._require_build_tool()

        temp_cdb_path = temp_compile_commands_path(cdb_dir)
        try:
            if self._kind is BuildSystemKind.MAKE:
                self._run_make(project_root, temp_cdb_path)
            else:
                self._run_autotools(project_root, temp_cdb_path)
            return read_compile_commands(temp_cdb_path)
        except (OSError, ValueError) as exc:
            raise RuntimeError(f"Bear produced invalid compile_commands.json: {exc}") from exc
        finally:
            temp_cdb_path.unlink(missing_ok=True)

    # --- internals ----------------------------------------------------

    def _fingerprint_inputs(self, project_root: Path) -> list[Path]:
        """Build markers + every C/C++ source under the project.

        Why: adding a new source must bust the cache so Bear re-runs and
        captures the compile command for it.
        """
        if self._kind is BuildSystemKind.MAKE:
            candidates = ("Makefile", "GNUmakefile", "makefile")
        else:
            candidates = ("configure.ac", "configure.in", "Makefile.am", "configure")
        out: list[Path] = [project_root / name for name in candidates]
        out.extend(collect_project_sources(project_root, CPP_SOURCE_EXTENSIONS))
        return out

    def _run_make(self, project_root: Path, cdb_path: Path) -> None:
        argv = [
            "bear",
            "--output",
            str(cdb_path),
            "--",
            "make",
            *config.make_target(),
        ]
        logger.info("Bear: running %s in %s", " ".join(argv), project_root)
        run_build_step(argv, cwd=project_root, step="bear make")

    def _run_autotools(self, project_root: Path, cdb_path: Path) -> None:
        if not (project_root / "configure").is_file():
            self._bootstrap_autotools(project_root)

        build_dir = project_root / CDB_SUBDIR / "_build"
        build_dir.mkdir(parents=True, exist_ok=True)
        configure_cmd = [str(project_root / "configure"), *config.configure_args()]
        run_build_step(configure_cmd, cwd=build_dir, step="./configure")

        argv = [
            "bear",
            "--output",
            str(cdb_path),
            "--",
            "make",
            *config.make_target(),
        ]
        run_build_step(argv, cwd=build_dir, step="bear make")

    @staticmethod
    def _bootstrap_autotools(project_root: Path) -> None:
        """Run the project's bootstrap script if present, else ``autoreconf -i``.

        Why: many GNU-tail repos (swig, glib, …) keep a custom ``autogen.sh``
        because raw ``autoreconf -i`` has known interactions with automake
        that the project's own script papers over.
        """
        for script in ("autogen.sh", "bootstrap.sh", "bootstrap"):
            p = project_root / script
            if p.is_file() and os.access(p, os.X_OK):
                run_build_step([f"./{script}"], cwd=project_root, step=script)
                return
        if shutil.which("autoreconf") is None:
            raise RuntimeError(
                "Autotools project has no ./configure script, no bootstrap script "
                "(autogen.sh / bootstrap.sh / bootstrap), and 'autoreconf' is not on PATH. "
                "Install autoconf/automake/libtool and retry."
            )
        run_build_step(["autoreconf", "-i"], cwd=project_root, step="autoreconf")

    @staticmethod
    def _require_bear() -> None:
        if shutil.which("bear") is None:
            raise RuntimeError(
                "'bear' is not on PATH. "
                "Install Bear 3.x (https://github.com/rizsotto/Bear) — "
                "'brew install bear' on macOS, 'apt install bear' on Debian/Ubuntu."
            )
        try:
            result = subprocess.run(
                ["bear", "--version"],
                capture_output=True,
                text=True,
                timeout=10,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise RuntimeError(f"Could not probe Bear version: {exc}") from exc
        if result.returncode != 0:
            raise RuntimeError(f"'bear --version' exited {result.returncode}: {result.stderr.strip()}")
        match = _BEAR_VERSION_RE.search(result.stdout) or _BEAR_VERSION_RE.search(result.stderr)
        if not match:
            # Some distros print an unusual banner; warn but let the run proceed.
            logger.warning("Could not parse Bear version from %r", (result.stdout + result.stderr).strip())
            return
        major = int(match.group(1))
        if major < _MIN_BEAR_MAJOR:
            raise RuntimeError(
                f"Bear {major}.x is too old — CodeBoarding requires Bear 3.x or later. "
                "Upgrade via your package manager."
            )

    def _require_build_tool(self) -> None:
        if shutil.which("make") is None:
            raise RuntimeError("'make' is not on PATH; install GNU Make and retry.")
