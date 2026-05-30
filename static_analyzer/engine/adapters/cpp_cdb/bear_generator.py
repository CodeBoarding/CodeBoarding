"""Compilation-database generator backed by Bear for Make and Autotools.

Bear intercepts compiler invocations and writes them to
``compile_commands.json``. Bear 3.x only (2.x CLI is incompatible).
"""

from __future__ import annotations

import logging
import re
import shutil
import subprocess
from pathlib import Path

from static_analyzer.engine.adapters.cpp_cdb import config
from static_analyzer.engine.adapters.cpp_cdb.base import (
    CDB_SUBDIR,
    CPP_SOURCE_EXTENSIONS,
    BuildSystemKind,
    CdbGenerator,
)
from static_analyzer.engine.adapters.cpp_cdb.cdb_io import (
    read_compile_commands,
    temp_compile_commands_path,
)
from static_analyzer.engine.adapters.cpp_cdb.fingerprint import collect_project_sources

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
        self._subprocess_run(argv, cwd=project_root, step="bear make")

    def _run_autotools(self, project_root: Path, cdb_path: Path) -> None:
        if not (project_root / "configure").is_file():
            if shutil.which("autoreconf") is None:
                raise RuntimeError(
                    "Autotools project has no ./configure script and 'autoreconf' is not on PATH. "
                    "Install autoconf/automake/libtool and retry."
                )
            self._subprocess_run(
                ["autoreconf", "-i"],
                cwd=project_root,
                step="autoreconf",
            )

        build_dir = project_root / CDB_SUBDIR / "_build"
        build_dir.mkdir(parents=True, exist_ok=True)
        configure_cmd = [str(project_root / "configure"), *config.configure_args()]
        self._subprocess_run(configure_cmd, cwd=build_dir, step="./configure")

        argv = [
            "bear",
            "--output",
            str(cdb_path),
            "--",
            "make",
            *config.make_target(),
        ]
        self._subprocess_run(argv, cwd=build_dir, step="bear make")

    @staticmethod
    def _subprocess_run(argv: list[str], *, cwd: Path, step: str) -> None:
        """Run a subprocess, surface stderr tail on failure, enforce the timeout."""
        try:
            result = subprocess.run(
                argv,
                cwd=str(cwd),
                capture_output=True,
                text=True,
                timeout=config.generator_timeout_seconds(),
            )
        except FileNotFoundError as exc:
            raise RuntimeError(f"{step}: command not found ({argv[0]})") from exc
        except OSError as exc:
            raise RuntimeError(f"{step}: could not run {argv[0]} ({exc})") from exc
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(
                f"{step} timed out after {config.generator_timeout_seconds()}s in {cwd}. "
                f"Raise {config.ENV_TIMEOUT} to allow more time."
            ) from exc

        if result.returncode != 0:
            # stderr tail only — a full build log would swamp the message.
            tail = (result.stderr or result.stdout or "").strip().splitlines()[-30:]
            raise RuntimeError(f"{step} failed with exit {result.returncode} in {cwd}:\n" + "\n".join(tail))

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
