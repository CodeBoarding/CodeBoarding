"""Abstract base for per-build-system compilation-database generators."""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from enum import Enum
from pathlib import Path


class BuildSystemKind(str, Enum):
    """Identifier for a detected C/C++ build system."""

    UNKNOWN = "unknown"
    CMAKE = "cmake"
    MESON = "meson"
    NINJA = "ninja"
    MAKE = "make"
    AUTOTOOLS = "autotools"
    BAZEL = "bazel"
    COMPILE_FLAGS_TXT = "compile_flags_txt"
    COMPILE_COMMANDS_JSON = "compile_commands_json"


CDB_SUBDIR = Path(".codeboarding") / "cdb"
"""Where generated compilation databases live, relative to the project root."""


CPP_SOURCE_EXTENSIONS: frozenset[str] = frozenset(
    {".cpp", ".cc", ".cxx", ".c++", ".ipp", ".tpp", ".hpp", ".hh", ".hxx", ".h++", ".h", ".c"}
)
"""File suffixes a CppAdapter would index."""


CDB_SKIP_DIRS: frozenset[str] = frozenset({".codeboarding", ".git", ".hg", ".svn", "node_modules", "__pycache__"})
"""Directories the fingerprint walker always prunes."""


# Intra-package imports follow the constants above so sibling modules
# (``fingerprint``, ``cdb_io``) can import those constants from us without
# tripping a circular-import error during package init.
from static_analyzer.engine.adapters.cpp_cdb import config  # noqa: E402
from static_analyzer.engine.adapters.cpp_cdb.cdb_io import (  # noqa: E402
    cdb_generation_lock,
    clear_generated_compile_commands,
    is_valid_compile_commands,
    write_compile_commands_atomic,
)
from static_analyzer.engine.adapters.cpp_cdb.fingerprint import (  # noqa: E402
    compute_fingerprint,
    delete_cached_fingerprint,
    read_cached_fingerprint,
    write_cached_fingerprint,
)


logger = logging.getLogger(__name__)


class CdbGenerator(ABC):
    """Produces a ``compile_commands.json`` by driving a build tool.

    Contract: ``generate`` writes
    ``<project_root>/.codeboarding/cdb/compile_commands.json`` and returns
    that path; it may raise :class:`RuntimeError` with a user-facing
    message that callers surface verbatim.
    """

    @property
    @abstractmethod
    def kind(self) -> BuildSystemKind:
        """The build system this generator handles."""

    @abstractmethod
    def _fingerprint_inputs(self, project_root: Path) -> list[Path]:
        """Files whose content changing should invalidate the cached CDB."""

    @abstractmethod
    def _build_entries(self, project_root: Path) -> list[dict]:
        """Run the build-specific commands and return CDB entries.

        May raise :class:`RuntimeError` with a user-facing message; the
        template method surfaces it verbatim.
        """

    def generate(self, project_root: Path) -> Path:
        """Generate a CDB for ``project_root`` and return its path.

        Template method: acquires the per-CDB lock, fingerprint-checks,
        delegates to :meth:`_build_entries`, writes atomically. Subclasses
        should not override.
        """
        cdb_dir = project_root / CDB_SUBDIR
        cdb_path = cdb_dir / "compile_commands.json"
        cdb_dir.mkdir(parents=True, exist_ok=True)

        with cdb_generation_lock(cdb_dir):
            new_fp = compute_fingerprint(self._fingerprint_inputs(project_root))
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

            entries = self._build_entries(project_root)

            try:
                write_compile_commands_atomic(cdb_path, entries)
            except (OSError, ValueError) as exc:
                raise RuntimeError(f"Could not write {self.kind} compile_commands.json: {exc}") from exc

            write_cached_fingerprint(cdb_dir, new_fp)
            return cdb_path
