"""Scanner for C# / .NET project configurations.

Detects solution files (.sln), project files (.csproj), and standalone
C# source trees to support mono-repo analysis with csharp-ls.
"""

import logging
from pathlib import Path

from repo_utils.ignore import RepoIgnoreManager

logger = logging.getLogger(__name__)


class CSharpProjectConfig:
    """Describes a discovered C# project root and its type."""

    def __init__(
        self,
        root: Path,
        project_type: str,  # "solution", "project", or "none"
    ):
        self.root = root
        self.project_type = project_type

    def __repr__(self) -> str:
        return f"CSharpProjectConfig(root={self.root}, project_type={self.project_type})"


class CSharpConfigScanner:
    """Scan a repository for C# / .NET project configurations.

    Scanning priority:
        1. ``.sln`` files — solution-level roots (csharp-ls uses these
           to discover all referenced projects automatically).
        2. Standalone ``.csproj`` files not already covered by a solution.
        3. Fallback to the repository root when ``.cs`` files exist but
           no solution or project files are found.
    """

    def __init__(self, repo_path: Path, ignore_manager: RepoIgnoreManager | None = None):
        self.repo_path = repo_path
        self.ignore_manager = ignore_manager if ignore_manager else RepoIgnoreManager(repo_path)

    def scan(self) -> list[CSharpProjectConfig]:
        """Return a list of C# project roots found in the repository."""
        configs: list[CSharpProjectConfig] = []

        # 1. Solution files (highest priority)
        solution_roots = self._find_solution_roots()
        for root in solution_roots:
            if not self.ignore_manager.should_ignore(root):
                configs.append(CSharpProjectConfig(root, "solution"))

        # 2. Standalone .csproj files not covered by a solution
        project_roots = self._find_project_roots()
        for root in project_roots:
            if self.ignore_manager.should_ignore(root):
                continue
            if any(self._is_subpath(root, c.root) for c in configs):
                continue
            configs.append(CSharpProjectConfig(root, "project"))

        # 3. Fallback: .cs files exist but no project infrastructure
        if not configs and self._has_cs_files(self.repo_path):
            logger.warning(
                f"No .sln or .csproj found in {self.repo_path}, but C# files detected. Analysis will be limited."
            )
            configs.append(CSharpProjectConfig(self.repo_path, "none"))

        return configs

    def _find_solution_roots(self) -> list[Path]:
        """Find directories containing .sln files."""
        return sorted({p.parent for p in self.repo_path.rglob("*.sln") if p.is_file()})

    def _find_project_roots(self) -> list[Path]:
        """Find directories containing .csproj files."""
        return sorted({p.parent for p in self.repo_path.rglob("*.csproj") if p.is_file()})

    def _has_cs_files(self, directory: Path) -> bool:
        """Check if directory contains any .cs files."""
        try:
            next(directory.rglob("*.cs"))
            return True
        except StopIteration:
            return False

    @staticmethod
    def _is_subpath(path: Path, parent: Path) -> bool:
        """Check if path is a subpath of (or equal to) parent."""
        try:
            path.relative_to(parent)
            return True
        except ValueError:
            return False
