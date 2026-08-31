"""Scanner for F# / .NET project configurations.

Detects solution files (.sln/.slnx), project files (.fsproj), and standalone
F# source trees to support mono-repo analysis with FsAutoComplete.
"""

import logging
from pathlib import Path

from repo_utils.ignore import RepoIgnoreManager

logger = logging.getLogger(__name__)

SOLUTION_GLOBS: tuple[str, ...] = ("*.sln", "*.slnx")


class FSharpProjectConfig:
    """Describes a discovered F# project root and its type."""

    def __init__(
        self,
        root: Path,
        project_type: str,  # "solution", "project", or "none"
    ):
        self.root = root
        self.project_type = project_type

    def __repr__(self) -> str:
        return f"FSharpProjectConfig(root={self.root}, project_type={self.project_type})"


class FSharpConfigScanner:
    """Scan a repository for F# / .NET project configurations.

    Scanning priority:
        1. ``.sln`` / ``.slnx`` files — solution-level roots, which
           FsAutoComplete uses to discover all referenced projects.
        2. Standalone ``.fsproj`` files not already covered by a solution.
        3. Fallback to the repository root when ``.fs`` files exist but no
           solution or project files are found.

    Why the ``.fs`` filter matters here: a solution carries both languages, so
    the C# scanner sees every F#-only solution and this one sees every C#-only
    solution. Dropping roots that ship none of our own sources keeps each
    scanner to the solutions it can actually say something about.
    """

    def __init__(self, repo_path: Path, ignore_manager: RepoIgnoreManager | None = None):
        self.repo_path = repo_path
        self.ignore_manager = ignore_manager if ignore_manager else RepoIgnoreManager(repo_path)

    def scan(self) -> list[FSharpProjectConfig]:
        """Return a list of F# project roots found in the repository."""
        configs: list[FSharpProjectConfig] = []

        # 1. Solution files (highest priority)
        for root in self._find_solution_roots():
            if not self.ignore_manager.should_ignore(root):
                configs.append(FSharpProjectConfig(root, "solution"))

        # 2. Standalone .fsproj files not covered by a solution
        for root in self._find_project_roots():
            if self.ignore_manager.should_ignore(root):
                continue
            if any(self._is_subpath(root, c.root) for c in configs):
                continue
            configs.append(FSharpProjectConfig(root, "project"))

        # A root with no F# source still costs a dotnet restore and a full
        # workspace load to produce nothing.
        sourceless = [c for c in configs if not self._has_fs_files(c.root)]
        if sourceless:
            configs = [c for c in configs if c not in sourceless]
            logger.info(
                "Skipping %d F# project root(s) with no .fs files: %s",
                len(sourceless),
                ", ".join(str(c.root.name) for c in sourceless[:5]) + ("..." if len(sourceless) > 5 else ""),
            )

        # 3. Fallback: .fs files exist but no project infrastructure
        if not configs and self._has_fs_files(self.repo_path):
            logger.warning(
                f"No .sln/.slnx or .fsproj found in {self.repo_path}, but F# files detected. "
                "Analysis will be limited."
            )
            configs.append(FSharpProjectConfig(self.repo_path, "none"))

        return configs

    def _find_solution_roots(self) -> list[Path]:
        """Find directories containing ``.sln`` or ``.slnx`` files."""
        roots: set[Path] = set()
        for pattern in SOLUTION_GLOBS:
            roots.update(p.parent for p in self.repo_path.rglob(pattern) if p.is_file())
        return sorted(roots)

    def _find_project_roots(self) -> list[Path]:
        """Find directories containing .fsproj files."""
        return sorted({p.parent for p in self.repo_path.rglob("*.fsproj") if p.is_file()})

    def _has_fs_files(self, directory: Path) -> bool:
        """Whether the directory holds any .fs file the analysis would read.

        Ignored paths do not count, so a repo whose only F# sits in a vendored
        directory does not drag the whole root into the analysis.
        """
        return any(not self.ignore_manager.should_ignore(path) for path in directory.rglob("*.fs"))

    @staticmethod
    def _is_subpath(path: Path, parent: Path) -> bool:
        """Check if path is a subpath of (or equal to) parent."""
        try:
            path.relative_to(parent)
            return True
        except ValueError:
            return False
