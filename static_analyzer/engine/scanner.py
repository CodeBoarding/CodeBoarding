"""Project scanner for language detection and source file discovery."""

from __future__ import annotations

import logging
from pathlib import Path

from static_analyzer.engine.language_adapter import LanguageAdapter

logger = logging.getLogger(__name__)


class EngineProjectScanner:
    """Scans a project directory to detect languages and discover source files."""

    def __init__(self, project_root: Path, adapters: dict[str, LanguageAdapter]) -> None:
        self._project_root = project_root.resolve()
        self._adapters = adapters

    def scan(self) -> dict[str, list[Path]]:
        """Scan the project and return source files grouped by language."""
        result: dict[str, list[Path]] = {}

        for language, adapter in self._adapters.items():
            files = self._discover_files(adapter)
            if files:
                result[language] = files
                logger.info("Found %d %s files in %s", len(files), language, self._project_root)

        return result

    def _discover_files(self, adapter: LanguageAdapter) -> list[Path]:
        """Discover all source files for a given language adapter."""
        excluded = adapter.get_excluded_dirs()
        extensions = set(adapter.file_extensions)
        files: list[Path] = []

        for path in self._walk_with_exclusions(self._project_root, excluded):
            if path.suffix in extensions and not adapter.is_test_file(path):
                files.append(path)

        files.sort()
        return files

    def _walk_with_exclusions(self, root: Path, excluded_dirs: set[str]):
        """Walk directory tree, skipping excluded directories."""
        try:
            entries = sorted(root.iterdir())
        except PermissionError:
            return

        for entry in entries:
            if entry.is_dir():
                if entry.name not in excluded_dirs and not entry.name.startswith("."):
                    yield from self._walk_with_exclusions(entry, excluded_dirs)
            elif entry.is_file():
                yield entry
