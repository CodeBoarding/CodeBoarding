"""Abstract base for per-build-system compilation-database generators."""

from __future__ import annotations

from abc import ABC, abstractmethod
from enum import Enum
from pathlib import Path


class BuildSystemKind(str, Enum):
    """Identifier for a detected C/C++ build system.

    Values are stable and safe to log; the ``str`` base lets them appear
    directly in error messages without ``.value``.
    """

    UNKNOWN = "unknown"
    CMAKE = "cmake"
    MESON = "meson"
    NINJA = "ninja"
    MAKE = "make"
    AUTOTOOLS = "autotools"
    BAZEL = "bazel"
    COMPILE_FLAGS_TXT = "compile_flags_txt"
    COMPILE_COMMANDS_JSON = "compile_commands_json"


class CdbGenerator(ABC):
    """Produces a ``compile_commands.json`` by driving a build tool.

    Concrete subclasses (BearGenerator, BazelAqueryGenerator) encapsulate
    one build system each. The dispatcher picks a subclass based on the
    result of :func:`detect_build_system` and calls :meth:`generate`.

    Contract:
      * ``generate`` writes ``<project_root>/.codeboarding/cdb/compile_commands.json``
        and returns that path.
      * ``generate`` may raise :class:`RuntimeError` with a user-facing
        message — callers surface it verbatim.
      * Generators are responsible for their own caching via
        :mod:`.fingerprint`; a cache hit short-circuits the build.
    """

    @property
    @abstractmethod
    def kind(self) -> BuildSystemKind:
        """The build system this generator handles."""

    @abstractmethod
    def generate(self, project_root: Path) -> Path:
        """Generate a CDB for ``project_root`` and return its path.

        Must create ``project_root / ".codeboarding" / "cdb"`` if missing.
        """

    @abstractmethod
    def describe_install(self) -> str:
        """One-line hint: how the user installs the required tools.

        Surfaced in the RuntimeError when the required toolchain (``bear``,
        ``bazel``, etc.) is missing from ``$PATH``.
        """


CDB_SUBDIR = Path(".codeboarding") / "cdb"
"""Where generated compilation databases live, relative to the project root.

Chosen over the repo root so generators never collide with editor-side
``compile_commands.json`` the user may already commit.
"""


CPP_SOURCE_EXTENSIONS: frozenset[str] = frozenset(
    {".cpp", ".cc", ".cxx", ".c++", ".ipp", ".tpp", ".hpp", ".hh", ".hxx", ".h++", ".h", ".c"}
)
"""File suffixes a CppAdapter would index.

Defined here (not on the adapter) so the fingerprint walker can live in
``cpp_cdb`` without a circular import.
"""


CDB_SKIP_DIRS: frozenset[str] = frozenset({".codeboarding", ".git", ".hg", ".svn", "node_modules", "__pycache__"})
"""Directories the fingerprint walker always prunes.

Adding a file under any of these should never invalidate a CDB cache —
they contain tooling output, VCS metadata, or dependency installs that
don't affect the build graph.
"""
