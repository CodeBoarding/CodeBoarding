import logging
from collections.abc import Set as AbstractSet
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from repo_utils.ignore import RepoIgnoreManager

logger = logging.getLogger(__name__)


class Ecosystem(StrEnum):
    PYTHON = "python"
    NODE = "node"
    GO = "go"
    JAVA = "java"
    PHP = "php"


class FileRole(StrEnum):
    MANIFEST = "manifest"
    LOCK = "lock"
    CONFIG = "config"


@dataclass(frozen=True, slots=True)
class DependencyFileSpec:
    filename: str
    ecosystem: Ecosystem
    role: FileRole


DEPENDENCY_REGISTRY: tuple[DependencyFileSpec, ...] = (
    # ── Python ──
    DependencyFileSpec("requirements.txt", Ecosystem.PYTHON, FileRole.MANIFEST),
    DependencyFileSpec("requirements-dev.txt", Ecosystem.PYTHON, FileRole.MANIFEST),
    DependencyFileSpec("requirements-test.txt", Ecosystem.PYTHON, FileRole.MANIFEST),
    DependencyFileSpec("dev-requirements.txt", Ecosystem.PYTHON, FileRole.MANIFEST),
    DependencyFileSpec("test-requirements.txt", Ecosystem.PYTHON, FileRole.MANIFEST),
    DependencyFileSpec("setup.py", Ecosystem.PYTHON, FileRole.MANIFEST),
    DependencyFileSpec("setup.cfg", Ecosystem.PYTHON, FileRole.MANIFEST),
    DependencyFileSpec("Pipfile", Ecosystem.PYTHON, FileRole.MANIFEST),
    DependencyFileSpec("Pipfile.lock", Ecosystem.PYTHON, FileRole.LOCK),
    DependencyFileSpec("pyproject.toml", Ecosystem.PYTHON, FileRole.MANIFEST),
    DependencyFileSpec("poetry.lock", Ecosystem.PYTHON, FileRole.LOCK),
    DependencyFileSpec("pdm.lock", Ecosystem.PYTHON, FileRole.LOCK),
    DependencyFileSpec("uv.lock", Ecosystem.PYTHON, FileRole.LOCK),
    DependencyFileSpec("environment.yml", Ecosystem.PYTHON, FileRole.MANIFEST),
    DependencyFileSpec("environment.yaml", Ecosystem.PYTHON, FileRole.MANIFEST),
    DependencyFileSpec("conda.yml", Ecosystem.PYTHON, FileRole.MANIFEST),
    DependencyFileSpec("conda.yaml", Ecosystem.PYTHON, FileRole.MANIFEST),
    DependencyFileSpec("pixi.toml", Ecosystem.PYTHON, FileRole.MANIFEST),
    DependencyFileSpec("requirements.in", Ecosystem.PYTHON, FileRole.MANIFEST),
    DependencyFileSpec("pixi.lock", Ecosystem.PYTHON, FileRole.LOCK),
    # ── Node / TypeScript / JavaScript ──
    DependencyFileSpec("package.json", Ecosystem.NODE, FileRole.MANIFEST),
    DependencyFileSpec("package-lock.json", Ecosystem.NODE, FileRole.LOCK),
    DependencyFileSpec("yarn.lock", Ecosystem.NODE, FileRole.LOCK),
    DependencyFileSpec("pnpm-lock.yaml", Ecosystem.NODE, FileRole.LOCK),
    DependencyFileSpec("bun.lockb", Ecosystem.NODE, FileRole.LOCK),
    DependencyFileSpec("tsconfig.json", Ecosystem.NODE, FileRole.CONFIG),
    DependencyFileSpec("jsconfig.json", Ecosystem.NODE, FileRole.CONFIG),
    DependencyFileSpec("bun.lock", Ecosystem.NODE, FileRole.LOCK),
    DependencyFileSpec("deno.json", Ecosystem.NODE, FileRole.MANIFEST),
    DependencyFileSpec("deno.jsonc", Ecosystem.NODE, FileRole.MANIFEST),
    DependencyFileSpec("deno.lock", Ecosystem.NODE, FileRole.LOCK),
    DependencyFileSpec("lerna.json", Ecosystem.NODE, FileRole.CONFIG),
    # ── Go ──
    DependencyFileSpec("go.mod", Ecosystem.GO, FileRole.MANIFEST),
    DependencyFileSpec("go.sum", Ecosystem.GO, FileRole.LOCK),
    DependencyFileSpec("go.work", Ecosystem.GO, FileRole.CONFIG),
    DependencyFileSpec("go.work.sum", Ecosystem.GO, FileRole.LOCK),
    # ── Java / JVM ──
    DependencyFileSpec("pom.xml", Ecosystem.JAVA, FileRole.MANIFEST),
    DependencyFileSpec("pom.properties", Ecosystem.JAVA, FileRole.CONFIG),
    DependencyFileSpec("build.gradle", Ecosystem.JAVA, FileRole.MANIFEST),
    DependencyFileSpec("build.gradle.kts", Ecosystem.JAVA, FileRole.MANIFEST),
    DependencyFileSpec("settings.gradle", Ecosystem.JAVA, FileRole.CONFIG),
    DependencyFileSpec("settings.gradle.kts", Ecosystem.JAVA, FileRole.CONFIG),
    DependencyFileSpec("gradle.properties", Ecosystem.JAVA, FileRole.CONFIG),
    DependencyFileSpec("build.sbt", Ecosystem.JAVA, FileRole.MANIFEST),
    DependencyFileSpec("gradle.lockfile", Ecosystem.JAVA, FileRole.LOCK),
    DependencyFileSpec("verification-metadata.xml", Ecosystem.JAVA, FileRole.LOCK),
    # ── PHP ──
    DependencyFileSpec("composer.json", Ecosystem.PHP, FileRole.MANIFEST),
    DependencyFileSpec("composer.lock", Ecosystem.PHP, FileRole.LOCK),
    DependencyFileSpec("symfony.lock", Ecosystem.PHP, FileRole.LOCK),
    DependencyFileSpec("phive.xml", Ecosystem.PHP, FileRole.MANIFEST),
    DependencyFileSpec("package.xml", Ecosystem.PHP, FileRole.MANIFEST),
)

DEPENDENCY_FILES: tuple[str, ...] = tuple(spec.filename for spec in DEPENDENCY_REGISTRY)

_FILENAME_TO_SPEC: dict[str, DependencyFileSpec] = {spec.filename: spec for spec in DEPENDENCY_REGISTRY}


@dataclass
class DiscoveredDependencyFile:
    path: Path
    spec: DependencyFileSpec


def discover_dependency_files(
    repo_dir: Path,
    ignore_manager: RepoIgnoreManager,
    *,
    max_depth: int = 3,
    roles: AbstractSet[FileRole] | None = None,
    ecosystems: AbstractSet[Ecosystem] | None = None,
) -> list[DiscoveredDependencyFile]:
    """Discover dependency files with full ecosystem / role metadata.

    Walks the repository tree up to *max_depth* directories deep,
    matching filenames against the known dependency registry in O(1)
    per file.  The *ignore_manager* prunes entire subtrees early.

    Args:
        repo_dir: Repository root.
        ignore_manager: Ignore-rule evaluator (gitignore, codeboardingignore).
        max_depth: Maximum directory depth to descend (0 = root only).
        roles: If given, only return files whose role is in this set.
        ecosystems: If given, only return files whose ecosystem is in this set.
    """
    found: list[DiscoveredDependencyFile] = []
    seen: set[Path] = set()

    def _walk(directory: Path, depth: int) -> None:
        if depth > max_depth:
            return
        if directory != repo_dir and ignore_manager.should_ignore(directory):
            return
        try:
            entries = sorted(directory.iterdir())
        except PermissionError:
            return

        for entry in entries:
            if entry.is_file():
                spec = _FILENAME_TO_SPEC.get(entry.name)
                if spec is None:
                    continue
                if roles and spec.role not in roles:
                    continue
                if ecosystems and spec.ecosystem not in ecosystems:
                    continue
                if not ignore_manager.should_ignore(entry) and entry not in seen:
                    found.append(DiscoveredDependencyFile(path=entry, spec=spec))
                    seen.add(entry)
            elif entry.is_dir() and depth < max_depth:
                _walk(entry, depth + 1)

    _walk(repo_dir, 0)

    logger.debug(
        "[Dependency Discovery] Found %d dependency files: %s",
        len(found),
        ", ".join(d.path.relative_to(repo_dir).as_posix() for d in found),
    )
    return found
