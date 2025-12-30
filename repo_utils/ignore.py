import logging
from pathlib import Path
import pathspec

logger = logging.getLogger(__name__)


class RepoIgnoreManager:
    """
    Centralized manager for handling file and directory exclusions across the repository.
    Combines .gitignore patterns with a default set of common directories to ignore.
    """

    DEFAULT_IGNORED_DIRS = {
        "node_modules",
        ".git",
        "__pycache__",
        "build",
        "dist",
        ".next",
        ".venv",
        "venv",
        "env",
        "temp",
        "repos",  # Specific to CodeBoarding context
        "runs",  # Monitoring runs
        "test",
        "tests",
    }

    # Build artifacts and minified files that should be ignored
    DEFAULT_IGNORED_FILE_PATTERNS = [
        "*.bundle.js",  # Webpack/bundler output
        "*.bundle.js.map",  # Source maps for bundles
        "*.min.js",  # Minified JavaScript
        "*.min.css",  # Minified CSS
        "*.chunk.js",  # Code-split chunks
        "*.chunk.js.map",  # Source maps for chunks
    ]

    def __init__(self, repo_root: Path):
        self.repo_root = repo_root.resolve()
        self.reload()

    def reload(self):
        """Reload ignore patterns from .gitignore."""
        self.spec = self._load_gitignore()

    def _load_gitignore(self) -> pathspec.PathSpec:
        """Load and parse .gitignore file if it exists."""
        gitignore_path = self.repo_root / ".gitignore"
        patterns = []

        if gitignore_path.exists():
            try:
                with gitignore_path.open("r", encoding="utf-8") as f:
                    patterns = f.readlines()
            except Exception as e:
                logger.warning(f"Failed to read .gitignore at {gitignore_path}: {e}")

        # Always add default ignored directories as patterns
        for d in self.DEFAULT_IGNORED_DIRS:
            patterns.append(f"{d}/\n")

        # Add default ignored file patterns (build artifacts, minified files, etc.)
        for pattern in self.DEFAULT_IGNORED_FILE_PATTERNS:
            patterns.append(f"{pattern}\n")

        return pathspec.PathSpec.from_lines("gitwildmatch", patterns)

    def should_ignore(self, path: Path) -> bool:
        """
        Check if a given path should be ignored.
        Handles both absolute paths and paths relative to repo_root.
        """
        try:
            # Convert to relative path if absolute
            if path.is_absolute():
                path = path.resolve()
                if not path.is_relative_to(self.repo_root):
                    # If it's absolute but outside repo_root, we might still want to check
                    # but for now let's assume it's relative to current working dir or similar.
                    # Usually we only care about paths inside the repo.
                    return False
                rel_path = path.relative_to(self.repo_root)
            else:
                rel_path = path

            # Check if any part of the path is in DEFAULT_IGNORED_DIRS
            # (pathspec might not catch nested node_modules if not specified with **/node_modules/**)
            for part in rel_path.parts:
                if part in self.DEFAULT_IGNORED_DIRS:
                    return True
                if part.startswith("."):
                    # Generally ignore hidden directories (except maybe some specific ones if needed)
                    # This matches the existing logic in LSPClient
                    return True

            # Use pathspec for .gitignore patterns
            return self.spec.match_file(str(rel_path))
        except Exception as e:
            logger.error(f"Error checking ignore status for {path}: {e}")
            return False

    def filter_paths(self, paths: list[Path]) -> list[Path]:
        """Filter a list of paths, returning only those that should not be ignored."""
        return [p for p in paths if not self.should_ignore(p)]
