import logging
from pathlib import Path

import pathspec

logger = logging.getLogger(__name__)

CODEBOARDINGIGNORE_TEMPLATE = """# CodeBoarding Ignore File
# Add patterns here for files and directories that should be excluded from CodeBoarding analysis.
# Use the same format as .gitignore (gitignore syntax / gitwildmatch patterns).
#
# To stop ignoring a pattern, prefix it with ! (e.g., !important_file.txt)
#
# NOTE: The following are ALWAYS excluded (not configurable):
#   - Hidden directories (starting with .)
#   - .git/, .codeboarding/, node_modules/, __pycache__/
#   - Build output: build/, dist/, coverage/
#
# This file is automatically loaded by CodeBoarding analysis tools to exclude
# specified paths from code analysis, architecture generation, and other processing.

# ============================================================================
# Ignored directories (customizable — remove lines to include them)
# ============================================================================

# Python virtual environments
venv/
env/
*.egg-info/

# Java (Maven/Gradle) and Rust (Cargo) build output. Both ecosystems
# produce a top-level ``target/`` directory full of compiled artifacts —
# kept here as well as in ``_ALWAYS_IGNORED_DIRS`` so users who customize
# their ``.codeboardingignore`` continue to skip it even after edits.
target/
bin/
out/

# .NET / C# build output
obj/

# Go
vendor/
testdata/

# PHP
cache/

# Custom
temp/
repos/
runs/

# ============================================================================
# Test and infrastructure files
# ============================================================================

# Test directories
**/__tests__/**
**/tests/**
**/test/**
**/__test__/**
**/testing/**
**/testutil/**

# Java/Kotlin test directories (Maven/Gradle structure)
**/src/test/**
**/src/testFixtures/**
**/src/integration-test/**
**/src/jmh/**
**/src/contractTest/**
**/osgi-tests/**

# Test files by naming convention
*.test.*
*.spec.*
*_test.*
*test_*.py
test_*.py
*Test.java
*IT.java
*Test.kt
*IT.kt
*Tests.java

# Mock, fixture, and stub directories
**/__mocks__/**
**/mocks/**
**/fixtures/**
**/fixture/**
**/stubs/**
**/stub/**
**/fakes/**
**/fake/**

# E2E and integration test directories
**/e2e/**
**/integration-tests/**
**/integration_test*/**

# ============================================================================
# Non-production code
# ============================================================================

# Example and documentation code
**/examples/**
**/documentation/examples/**

# Generated code
*.pb.go
**/generated_parser*

# Java/Kotlin metadata files
module-info.java

# ============================================================================
# Build artifacts and minified files
# ============================================================================

*.bundle.js
*.bundle.js.map
*.min.js
*.min.css
*.chunk.js
*.chunk.js.map

# ============================================================================
# Build tool configs and infrastructure
# ============================================================================

esbuild*
webpack*
rollup*
vite.config.*
gulpfile*
gruntfile*
*.config.*
"""


# Compiled pathspec from the template — used by RepoIgnoreManager.should_skip_file()
_DEFAULT_SPEC = pathspec.PathSpec.from_lines("gitwildmatch", CODEBOARDINGIGNORE_TEMPLATE.splitlines())


# Directories that are always excluded, even without a .codeboardingignore file.
# These contain compiled output, dependency installs, or tooling artifacts —
# never source code, regardless of language or user preference.
_ALWAYS_IGNORED_DIRS = {
    # Version control and tooling
    ".git",
    ".codeboarding",
    # Dependency installs
    "node_modules",
    # Compiled / build output (universal across ecosystems — never source)
    "__pycache__",
    "build",
    "dist",
    "coverage",
    "target",  # Java (Maven), Rust (Cargo)
}


class RepoIgnoreManager:
    """Centralized manager for handling file and directory exclusions across the repository.

    Combines patterns from .gitignore and .codeboardingignore. Default exclusion
    patterns are defined in the CODEBOARDINGIGNORE_TEMPLATE and written to
    ``.codeboarding/.codeboardingignore`` on first run — users can then
    customize which patterns to keep, remove, or add.
    """

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

        # Combined spec for the should_ignore() fast path
        all_patterns = list(gitignore_patterns)
        all_patterns.extend(codeboardingignore_patterns)
        self.spec = pathspec.PathSpec.from_lines("gitwildmatch", all_patterns)

    def _load_gitignore_patterns(self) -> list[str]:
        """Load and parse .gitignore file if it exists."""
        gitignore_path = self.repo_root / ".gitignore"

        if gitignore_path.exists():
            try:
                with gitignore_path.open("r", encoding="utf-8") as f:
                    return f.readlines()
            except Exception as e:
                logger.warning(f"Failed to read .gitignore at {gitignore_path}: {e}")

        return []

    def _load_codeboardingignore_patterns(self) -> list[str]:
        """Load and parse .codeboardingignore file from .codeboarding directory.

        If the file does not exist, returns the default template patterns so that
        first-run analysis still has sensible exclusions even before
        ``initialize_codeboardingignore`` is called.
        """
        codeboardingignore_path = self.repo_root / ".codeboarding" / ".codeboardingignore"

        if codeboardingignore_path.exists():
            try:
                with codeboardingignore_path.open("r", encoding="utf-8") as f:
                    return f.readlines()
            except Exception as e:
                logger.warning(f"Failed to read .codeboardingignore at {codeboardingignore_path}: {e}")

        # Fall back to the default template so analysis works before the file is created
        return list(CODEBOARDINGIGNORE_TEMPLATE.splitlines(keepends=True))

    def should_ignore(self, path: Path) -> bool:
        """Check if a given path should be ignored.

        Handles both absolute paths and paths relative to repo_root.
        """
        try:
            # Convert to relative path if absolute
            if path.is_absolute():
                path = path.resolve()
                if not path.is_relative_to(self.repo_root):
                    return False
                rel_path = path.relative_to(self.repo_root)
            else:
                rel_path = path

            # Always exclude hidden directories (starting with .) and a small
            # set of universally non-source directories. Everything else is
            # handled by pathspec patterns from .codeboardingignore.
            for part in rel_path.parts:
                if part in _ALWAYS_IGNORED_DIRS:
                    return True
                if part.startswith("."):
                    return True

            # Use pathspec for .gitignore + .codeboardingignore patterns
            return self.spec.match_file(str(rel_path))
        except Exception as e:
            logger.error(f"Error checking ignore status for {path}: {e}")
            return False

    def filter_paths(self, paths: list[Path]) -> list[Path]:
        """Filter a list of paths, returning only those that should not be ignored."""
        return [p for p in paths if not self.should_ignore(p)]

    @staticmethod
    def should_skip_file(file_path: str | Path | None) -> bool:
        """Check if a file path matches default exclusion patterns.

        Standalone check for contexts where no RepoIgnoreManager instance is
        available (health checks, incremental analysis). Uses the default
        .codeboardingignore template patterns.
        """
        if not file_path:
            return False
        return _DEFAULT_SPEC.match_file(str(file_path))

    def categorize_file(self, path: Path) -> str:
        """Return the exclusion reason for a file.

        Reasons for excluded files: "ignored_directory", "codeboardingignore", "gitignore".
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
                if part in _ALWAYS_IGNORED_DIRS or part.startswith("."):
                    return "ignored_directory"

            rel_str = str(rel_path)
            if self.codeboardingignore_spec.match_file(rel_str):
                return "codeboardingignore"
            if self.gitignore_spec.match_file(rel_str):
                return "gitignore"

            return "other"
        except Exception as e:
            logger.error(f"Error categorizing file {path}: {e}")
            return "other"


def initialize_codeboardingignore(output_dir: Path) -> None:
    """Initialize .codeboardingignore file in the .codeboarding directory if it doesn't exist.

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
