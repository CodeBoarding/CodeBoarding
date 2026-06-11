"""Scanner for Swift / SwiftPM package configurations.

Detects ``Package.swift`` manifests across the repository so sourcekit-lsp
can be rooted at each package directory (its BSP walks up from ``didOpen``'d
files looking for ``Package.swift`` — when rooted at a parent that contains
no manifest, build settings resolve to nothing and cross-file queries return
empty results).
"""

import logging
from pathlib import Path

from repo_utils.ignore import RepoIgnoreManager

logger = logging.getLogger(__name__)


class SwiftPackageConfig:
    """Describes a discovered SwiftPM package root."""

    def __init__(self, root: Path):
        self.root = root

    def __repr__(self) -> str:
        return f"SwiftPackageConfig(root={self.root})"


class SwiftConfigScanner:
    """Scan a repository for SwiftPM package roots.

    Strategy:
        1. Locate every ``Package.swift`` outside ignored dependency/build dirs.
        2. Keep only top-level manifests: drop any package nested inside
           another discovered package (workspaces typically have a root
           Package.swift that already pulls in siblings via ``dependencies``).
        3. Fall back to the repository root when ``.swift`` files exist but
           no manifest is found (Xcode-only projects; analysis will be
           limited but the LSP still produces single-file symbols).
    """

    def __init__(self, repo_path: Path, ignore_manager: RepoIgnoreManager | None = None):
        self.repo_path = repo_path
        self.ignore_manager = ignore_manager if ignore_manager else RepoIgnoreManager(repo_path)

    def scan(self) -> list[SwiftPackageConfig]:
        configs: list[SwiftPackageConfig] = []

        for root in self._find_package_roots():
            if self.ignore_manager.should_ignore(root):
                continue
            # Skip if already covered by a previously-added (outer) package.
            if any(self._is_subpath(root, c.root) and root != c.root for c in configs):
                continue
            configs.append(SwiftPackageConfig(root))

        if not configs and self._has_swift_files(self.repo_path):
            logger.warning(
                f"No Package.swift found in {self.repo_path}, but Swift files detected. "
                "sourcekit-lsp cross-file queries will be empty without a SwiftPM manifest "
                "or compile_commands.json."
            )
            configs.append(SwiftPackageConfig(self.repo_path))

        return configs

    def _find_package_roots(self) -> list[Path]:
        """Return Package.swift parent dirs, sorted shallowest-first.

        Sorting by depth ensures outer packages appear before nested ones so
        the ``is_subpath`` filter in ``scan`` works in one pass.
        """
        roots: set[Path] = set()
        for manifest in self.repo_path.rglob("Package.swift"):
            if not manifest.is_file():
                continue
            if self.ignore_manager.should_ignore(manifest):
                continue
            roots.add(manifest.parent)
        return sorted(roots, key=lambda p: (len(p.parts), str(p)))

    def _has_swift_files(self, directory: Path) -> bool:
        try:
            for candidate in directory.rglob("*.swift"):
                if not self.ignore_manager.should_ignore(candidate):
                    return True
            return False
        except OSError:
            return False

    @staticmethod
    def _is_subpath(path: Path, parent: Path) -> bool:
        try:
            path.relative_to(parent)
            return True
        except ValueError:
            return False
