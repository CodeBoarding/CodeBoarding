import logging
import os
from fnmatch import fnmatch
from pathlib import Path

import pathspec

logger = logging.getLogger(__name__)

CODEBOARDINGIGNORE_TEMPLATE = """# CodeBoarding Ignore File
# Add patterns here for files and directories that should be excluded from CodeBoarding analysis.
# Use the same format as .gitignore (gitignore syntax / gitwildmatch patterns).
#
# Examples:
# - Ignore a specific directory: my_generated_files/
# - Ignore a file pattern: *.temp
# - Ignore nested patterns: **/cache/
# - Negate a pattern (stop ignoring): !important_file.txt
#
# This file is automatically loaded by CodeBoarding analysis tools to exclude
# specified paths from code analysis, architecture generation, and other processing.
"""

# Test and infrastructure patterns - used across health checks and file filtering
# These patterns identify files that are not production code (tests, mocks, fixtures)
TEST_INFRASTRUCTURE_PATTERNS = [
    # Test directories
    "*/__tests__/*",
    "*/tests/*",
    "tests/*",
    "*/test/*",
    "test/*",
    "*/__test__/*",
    "*/testing/*",
    # Java-specific test directories (Maven/Gradle structure)
    "*/src/test/*",
    "*/src/testFixtures/*",
    "*/src/integration-test/*",
    "*/src/jmh/*",  # JMH benchmark code
    "*/src/contractTest/*",
    # Test files by naming convention
    "*.test.*",
    "*.spec.*",
    "*_test.*",
    "*test_*.py",
    "test_*.py",
    "*Test.java",  # Java test classes (e.g., FooTest.java)
    "*IT.java",  # Java integration tests (e.g., FooIT.java)
    "*Test.kt",  # Kotlin test classes
    "*IT.kt",
    "*Tests.java",  # Java test classes (e.g., FooTests.java)
    # Mock and fixture directories
    "*/mock/*",
    "*/__mocks__/*",
    "*/mocks/*",
    "*/fixtures/*",
    "*/fixture/*",
    # Stubs and fake implementations
    "*/stubs/*",
    "*/stub/*",
    "*/fakes/*",
    "*/fake/*",
    # E2E and integration test directories
    "*/e2e/*",
    "*/integration-tests/*",
    "*/integration_test*/*",
    "*/osgi-tests/*",  # OSGi integration tests (seen in Mockito)
    # Development/infrastructure config
    "*.config.*",  # Only if in root? No, can be prod code too
]

# Build tool configs and infrastructure files matched by basename only.
# These are not production application code.
BUILD_CONFIG_PATTERNS = [
    "esbuild*",
    "webpack*",
    "rollup*",
    "vite*",
    "gulpfile*",
    "gruntfile*",
    "Makefile*",
    "Dockerfile*",
    "docker-compose*",
    "*.json",
]

# Documentation and configuration files that should be excluded from analysis
DOCUMENTATION_CONFIG_PATTERNS = [
    # Documentation files
    "README*",
    "CHANGELOG*",
    "LICENSE*",
    "CONTRIBUTING*",
    "*.md",
    "*.txt",
    "*.rst",
    # Config and lock files
    "*.yml",
    "*.yaml",
    "*.toml",
    "*.lock",
    ".gitignore",
    ".gitattributes",
    ".editorconfig",
    ".dockerignore",
    # Package/dependency files
    "setup.py",
    "setup.cfg",
    "requirements*.txt",
    "Pipfile",
    "Pipfile.lock",
    "uv.lock",
    "poetry.lock",
    # Build files
    "justfile",
    # Cache directories (matched as path components)
    "__pycache__",
    ".pytest_cache",
]


def is_test_or_infrastructure_file(file_path: str | Path | None) -> bool:
    """Check if a file path matches test, infrastructure, documentation, or config patterns.

    This is a standalone function that can be used without instantiating RepoIgnoreManager,
    making it suitable for use in health checks, incremental analysis, and other contexts
    where only a file path is available.

    Args:
        file_path: Path to check (string or Path object)

    Returns:
        True if the file is a test, mock, build config, documentation, or infrastructure file
    """
    if not file_path:
        return False

    path_str = str(file_path).lower()

    # Check test and infrastructure patterns
    for pattern in TEST_INFRASTRUCTURE_PATTERNS:
        if fnmatch(path_str, pattern.lower()):
            return True

    # Check basename against build/config patterns
    name = os.path.basename(path_str)
    for pattern in BUILD_CONFIG_PATTERNS:
        if fnmatch(name, pattern.lower()):
            return True

    # Check documentation and config patterns
    for pattern in DOCUMENTATION_CONFIG_PATTERNS:
        # Check both full path and basename
        if fnmatch(path_str, pattern.lower()) or fnmatch(name, pattern.lower()):
            return True

    return False


# Alias for compatibility with incremental analysis code
should_skip_file = is_test_or_infrastructure_file


class RepoIgnoreManager:
    """
    Centralized manager for handling file and directory exclusions across the repository.
    Combines patterns from .gitignore, .codeboardingignore, and a default set of common directories to ignore.
    """

    DEFAULT_IGNORED_DIRS = {
        ".codeboarding",
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
        # Test directories are handled via patterns below for more flexibility
    }

    # Build artifacts and minified files that should be ignored
    DEFAULT_IGNORED_FILE_PATTERNS = [
        "*.bundle.js",  # Webpack/bundler output
        "*.bundle.js.map",  # Source maps for bundles
        "*.min.js",  # Minified JavaScript
        "*.min.css",  # Minified CSS
        "*.chunk.js",  # Code-split chunks
        "*.chunk.js.map",  # Source maps for chunks
        # Test/infrastructure patterns are added dynamically
    ]

    def __init__(self, repo_root: Path):
        self.repo_root = repo_root.resolve()
        self.reload()

    def reload(self):
        """Reload ignore patterns from .gitignore and .codeboardingignore."""
        gitignore_patterns = self._load_gitignore_patterns()
        codeboardingignore_patterns = self._load_codeboardingignore_patterns()

        # Build separate specs for categorization
        self.gitignore_spec = pathspec.PathSpec.from_lines("gitwildmatch", gitignore_patterns)
        self.codeboardingignore_spec = pathspec.PathSpec.from_lines("gitwildmatch", codeboardingignore_patterns)

        default_patterns: list[str] = []
        for pattern in self.DEFAULT_IGNORED_FILE_PATTERNS:
            default_patterns.append(f"{pattern}\n")
        for pattern in TEST_INFRASTRUCTURE_PATTERNS:
            default_patterns.append(f"{pattern}\n")
        self.default_spec = pathspec.PathSpec.from_lines("gitwildmatch", default_patterns)

        # Combined spec for the existing should_ignore() fast path
        all_patterns = list(gitignore_patterns)
        all_patterns.extend(codeboardingignore_patterns)
        for d in self.DEFAULT_IGNORED_DIRS:
            all_patterns.append(f"{d}/\n")
        all_patterns.extend(default_patterns)

        self.spec = pathspec.PathSpec.from_lines("gitwildmatch", all_patterns)

    def _load_gitignore_patterns(self) -> list[str]:
        """Load and parse .gitignore file if it exists."""
        gitignore_path = self.repo_root / ".gitignore"
        patterns = []

        if gitignore_path.exists():
            try:
                with gitignore_path.open("r", encoding="utf-8") as f:
                    patterns = f.readlines()
            except Exception as e:
                logger.warning(f"Failed to read .gitignore at {gitignore_path}: {e}")

        return patterns

    def _load_codeboardingignore_patterns(self) -> list[str]:
        """Load and parse .codeboardingignore file from .codeboarding directory if it exists."""
        codeboardingignore_path = self.repo_root / ".codeboarding" / ".codeboardingignore"
        patterns = []

        if codeboardingignore_path.exists():
            try:
                with codeboardingignore_path.open("r", encoding="utf-8") as f:
                    patterns = f.readlines()
            except Exception as e:
                logger.warning(f"Failed to read .codeboardingignore at {codeboardingignore_path}: {e}")

        return patterns

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

    def categorize_file(self, path: Path) -> str:
        """Return the exclusion reason for a file.

        Reasons for excluded files: "ignored_directory", "test_or_infrastructure",
        "codeboardingignore", "gitignore".
        Returns "other" if the file is not excluded by any known rule.
        """
        try:
            if path.is_absolute():
                path = path.resolve()
                if not path.is_relative_to(self.repo_root):
                    return "other"
                rel_path = path.relative_to(self.repo_root)
            else:
                rel_path = path

            for part in rel_path.parts:
                if part in self.DEFAULT_IGNORED_DIRS or part.startswith("."):
                    return "ignored_directory"

            rel_str = str(rel_path)
            if self.default_spec.match_file(rel_str):
                return "test_or_infrastructure"
            if self.codeboardingignore_spec.match_file(rel_str):
                return "codeboardingignore"
            if self.gitignore_spec.match_file(rel_str):
                return "gitignore"

            return "other"
        except Exception as e:
            logger.error(f"Error categorizing file {path}: {e}")
            return "other"


def initialize_codeboardingignore(output_dir: Path) -> None:
    """
    Initialize .codeboardingignore file in the .codeboarding directory if it doesn't exist.

    Args:
        output_dir: Path to the .codeboarding directory
    """
    codeboardingignore_path = output_dir / ".codeboardingignore"

    if not codeboardingignore_path.exists():
        try:
            codeboardingignore_path.write_text(CODEBOARDINGIGNORE_TEMPLATE, encoding="utf-8")
            logger.debug(f"Created .codeboardingignore file at {codeboardingignore_path}")
        except Exception as e:
            logger.warning(f"Failed to create .codeboardingignore at {codeboardingignore_path}: {e}")
