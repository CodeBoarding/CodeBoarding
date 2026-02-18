from pathlib import Path

from repo_utils.ignore import RepoIgnoreManager

# Common dependency file patterns to search for in repository root.
DEPENDENCY_FILES: tuple[str, ...] = (
    "requirements.txt",
    "requirements-dev.txt",
    "requirements-test.txt",
    "dev-requirements.txt",
    "test-requirements.txt",
    "setup.py",
    "setup.cfg",
    "Pipfile",
    "environment.yml",
    "environment.yaml",
    "conda.yml",
    "conda.yaml",
    "pixi.toml",
    "uv.lock",
    # Node.js / TypeScript specific.
    "package.json",
    "package-lock.json",
    "yarn.lock",
    "pnpm-lock.yaml",
    "bun.lockb",
    # TypeScript compiler configuration (not dependencies, but relevant).
    "tsconfig.json",
)

# Common folders that often contain dependency manifests.
DEPENDENCY_SUBDIRS: tuple[str, ...] = ("requirements", "deps", "dependencies", "env")

# File patterns to scan only at the top level of each dependency subdirectory.
DEPENDENCY_SUBDIR_PATTERNS: tuple[str, ...] = ("*.txt", "*.yml", "*.yaml", "*.toml")


def discover_dependency_files(repo_dir: Path, ignore_manager: RepoIgnoreManager) -> list[Path]:
    """Discover dependency files in repo root and known dependency subdirectories."""
    found_files: list[Path] = []
    seen_files: set[Path] = set()

    for dep_file in DEPENDENCY_FILES:
        file_path = repo_dir / dep_file
        if file_path.exists() and file_path.is_file() and not ignore_manager.should_ignore(file_path):
            if file_path not in seen_files:
                found_files.append(file_path)
                seen_files.add(file_path)

    for subdir in DEPENDENCY_SUBDIRS:
        subdir_path = repo_dir / subdir
        if not subdir_path.exists() or not subdir_path.is_dir():
            continue
        if ignore_manager.should_ignore(subdir_path):
            continue

        for pattern in DEPENDENCY_SUBDIR_PATTERNS:
            for file_path in subdir_path.glob(pattern):
                if file_path.is_file() and not ignore_manager.should_ignore(file_path):
                    if file_path not in seen_files:
                        found_files.append(file_path)
                        seen_files.add(file_path)

    return found_files
