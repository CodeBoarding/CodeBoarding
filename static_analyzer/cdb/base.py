"""Abstract base for per-build-system compilation-database generators."""

from __future__ import annotations

import logging
import subprocess
from abc import ABC, abstractmethod
from enum import Enum
from pathlib import Path

from static_analyzer.constants import LANGUAGE_EXTENSIONS, Language


class BuildSystemKind(str, Enum):
    """Identifier for a buildable C/C++ build system.

    Why: only buildable kinds belong here. Pre-existing CDBs
    (``compile_commands.json`` / ``compile_flags.txt``) are returned as
    ``DetectionResult.existing_cdb`` by :func:`detect_build_system`, not
    as enum members — they have no generator and would otherwise leak
    into ``generator_for`` / ``install_hint_for`` as dead branches.
    """

    UNKNOWN = "unknown"
    CMAKE = "cmake"
    MESON = "meson"
    NINJA = "ninja"
    MAKE = "make"
    AUTOTOOLS = "autotools"
    BAZEL = "bazel"


CDB_SUBDIR = Path(".codeboarding") / "cdb"
"""Where generated compilation databases live, relative to the project root."""


CPP_SOURCE_EXTENSIONS: frozenset[str] = frozenset(LANGUAGE_EXTENSIONS[Language.CPP]) | frozenset(
    LANGUAGE_EXTENSIONS[Language.C]
)
"""Suffixes that clangd indexes (C and C++ — one process, one set)."""


CDB_SKIP_DIRS: frozenset[str] = frozenset({".codeboarding", ".git", ".hg", ".svn", "node_modules", "__pycache__"})
"""Directories the fingerprint walker always prunes."""


# Intra-package imports follow the constants above so sibling modules
# (``fingerprint``, ``cdb_io``) can import those constants from us without
# tripping a circular-import error during package init.
from static_analyzer.cdb import config  # noqa: E402
from static_analyzer.cdb.cdb_io import (  # noqa: E402
    cdb_generation_lock,
    clear_generated_compile_commands,
    is_valid_compile_commands,
    write_compile_commands_atomic,
)
from static_analyzer.cdb.fingerprint import (  # noqa: E402
    compute_fingerprint,
    delete_cached_fingerprint,
    read_cached_fingerprint,
    write_cached_fingerprint,
)


logger = logging.getLogger(__name__)


class CdbGenerator(ABC):
    """Produces a ``compile_commands.json`` by driving a build tool.

    Contract: ``generate`` writes
    ``<analysis_root>/.codeboarding/cdb/compile_commands.json`` and returns
    that path; it may raise :class:`RuntimeError` with a user-facing
    message that callers surface verbatim. ``build_cwd`` is the directory
    the build tool runs in (Stockfish-shape: Makefile in ``src/``); it
    defaults to ``analysis_root`` when the build files live at the repo
    root.
    """

    @property
    @abstractmethod
    def kind(self) -> BuildSystemKind:
        """The build system this generator handles."""

    @abstractmethod
    def _fingerprint_inputs(self, project_root: Path) -> list[Path]:
        """Files whose content changing should invalidate the cached CDB.

        ``project_root`` is the build cwd here: that's where the build
        marker (Makefile / BUILD) actually lives.
        """

    @abstractmethod
    def _build_entries(self, project_root: Path) -> list[dict]:
        """Run the build-specific commands and return CDB entries.

        ``project_root`` is the build cwd. May raise
        :class:`RuntimeError` with a user-facing message; the template
        method surfaces it verbatim.
        """

    def generate(self, analysis_root: Path, build_cwd: Path | None = None) -> Path:
        """Generate a CDB rooted at ``analysis_root`` and return its path.

        Template method: writes
        ``<analysis_root>/.codeboarding/cdb/compile_commands.json``
        regardless of where the build files live, so
        ``CppAdapter.get_lsp_command`` always finds it under the analysis
        root. Acquires the per-CDB lock, fingerprint-checks, delegates
        to :meth:`_build_entries`, writes atomically. Subclasses should
        not override. ``build_cwd`` defaults to ``analysis_root``.
        """
        cwd = build_cwd if build_cwd is not None else analysis_root
        cdb_dir = analysis_root / CDB_SUBDIR
        cdb_path = cdb_dir / "compile_commands.json"
        cdb_dir.mkdir(parents=True, exist_ok=True)

        with cdb_generation_lock(cdb_dir):
            metadata = [("__kind__", self.kind.value), *config.fingerprint_options()]
            new_fp = compute_fingerprint(self._fingerprint_inputs(cwd), metadata=metadata)
            if (
                not config.force_regenerate()
                and cdb_path.is_file()
                and read_cached_fingerprint(cdb_dir) == new_fp
                and is_valid_compile_commands(cdb_path)
            ):
                logger.info("%s CDB cache hit at %s (fingerprint %s)", self.kind, cdb_path, new_fp[:8])
                return cdb_path

            clear_generated_compile_commands(cdb_dir)
            delete_cached_fingerprint(cdb_dir)

            entries = self._build_entries(cwd)

            try:
                write_compile_commands_atomic(cdb_path, entries)
            except (OSError, ValueError) as exc:
                raise RuntimeError(f"Could not write {self.kind} compile_commands.json: {exc}") from exc

            write_cached_fingerprint(cdb_dir, new_fp)
            return cdb_path


def run_build_step(
    argv: list[str],
    *,
    cwd: Path,
    step: str,
    timeout: int | None = None,
    timeout_hint: str = "",
) -> subprocess.CompletedProcess[str]:
    """Run a build subprocess, raising ``RuntimeError`` with a stderr tail on failure.

    Why: every generator that shells out wants the same shape — capture
    output, surface a stderr tail on non-zero exit, and point at
    ``CODEBOARDING_CPP_GENERATOR_TIMEOUT`` when the wall clock expires.
    ``timeout`` defaults to ``config.generator_timeout_seconds()``.
    ``timeout_hint`` is appended to the timeout message so generators with
    their own scoping knob (e.g. Bazel's query) can point users at it
    alongside ``ENV_TIMEOUT``.
    """
    effective_timeout = timeout if timeout is not None else config.generator_timeout_seconds()
    try:
        # Why: errors="replace" so localized toolchains emitting non-UTF-8
        # bytes (CP1252 Windows make, etc.) don't crash subprocess decode.
        result = subprocess.run(
            argv,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            errors="replace",
            timeout=effective_timeout,
        )
    except FileNotFoundError as exc:
        raise RuntimeError(f"{step}: command not found ({argv[0]})") from exc
    except OSError as exc:
        raise RuntimeError(f"{step}: could not run {argv[0]} ({exc})") from exc
    except subprocess.TimeoutExpired as exc:
        message = (
            f"{step} timed out after {effective_timeout}s in {cwd}. " f"Raise {config.ENV_TIMEOUT} to allow more time."
        )
        if timeout_hint:
            message = f"{message} {timeout_hint}"
        raise RuntimeError(message) from exc

    if result.returncode != 0:
        # stderr tail only — a full build log would swamp the message.
        tail = (result.stderr or result.stdout or "").strip().splitlines()[-30:]
        raise RuntimeError(f"{step} failed with exit {result.returncode} in {cwd}:\n" + "\n".join(tail))
    return result
