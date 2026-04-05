"""Repository configurations and auto-clone helper for incremental benchmarks.

Each scenario set targets a specific repo at a pinned tag so that the
deterministic string replacements in the scenario ``content_fn`` callbacks
always find the expected content.
"""

import logging
import subprocess
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

# Submodule location: tests/integration/benchmark_repos/<name>
CODEBOARDING_ROOT = Path(__file__).resolve().parent.parent.parent.parent
BENCHMARK_REPOS_DIR = Path(__file__).resolve().parent.parent / "benchmark_repos"

# Fallback clone location: sibling directory to the CodeBoarding repo root.
DEFAULT_CLONE_PARENT = CODEBOARDING_ROOT.parent


@dataclass(frozen=True)
class RepoConfig:
    name: str
    repo_url: str
    pinned_tag: str


REPO_CONFIGS: dict[str, RepoConfig] = {
    "deepface": RepoConfig(
        name="deepface",
        repo_url="https://github.com/serengil/deepface.git",
        pinned_tag="v0.0.97",
    ),
    "langchain": RepoConfig(
        name="langchain",
        repo_url="https://github.com/langchain-ai/langchain.git",
        pinned_tag="langchain-core==1.2.20",
    ),
    "markitdown": RepoConfig(
        name="markitdown",
        repo_url="https://github.com/microsoft/markitdown.git",
        pinned_tag="v0.1.3",
    ),
    "jsoup": RepoConfig(
        name="jsoup",
        repo_url="https://github.com/jhy/jsoup.git",
        pinned_tag="jsoup-1.22.1",
    ),
    "zustand": RepoConfig(
        name="zustand",
        repo_url="https://github.com/pmndrs/zustand.git",
        pinned_tag="v5.0.12",
    ),
}


def _git(*args: str, cwd: Path) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout


def _head_matches_tag(repo_dir: Path, tag: str) -> bool:
    """Return True if HEAD already points at *tag*."""
    try:
        head = _git("rev-parse", "HEAD", cwd=repo_dir).strip()
        tag_sha = _git("rev-parse", f"refs/tags/{tag}^{{}}", cwd=repo_dir).strip()
        return head == tag_sha
    except RuntimeError:
        # Tag not fetched yet or not a valid ref — caller will fix up.
        return False


def _is_valid_repo(repo_dir: Path, tag: str) -> bool:
    """Return True if *repo_dir* is a git repo at (or near) the expected *tag*."""
    if not repo_dir.exists():
        return False
    # Submodules have .git as a file, not a directory
    git_marker = repo_dir / ".git"
    if not git_marker.exists():
        return False
    return _head_matches_tag(repo_dir, tag)


def ensure_repo(config: RepoConfig, parent_dir: Path | None = None) -> Path:
    """Return a path to *config.name* checked out at *config.pinned_tag*.

    Resolution order:
    1. Git submodule at ``tests/integration/benchmark_repos/<name>`` (preferred).
    2. Existing clone under *parent_dir* (or sibling directory).
    3. Fresh shallow clone as a fallback.

    Returns the absolute path to the repo working directory.
    """
    # 1. Prefer submodule
    submodule_dir = BENCHMARK_REPOS_DIR / config.name
    if _is_valid_repo(submodule_dir, config.pinned_tag):
        logger.info("Using submodule at %s", submodule_dir)
        return submodule_dir

    # Submodule exists but not initialised or at wrong commit — try to init it
    if (BENCHMARK_REPOS_DIR / config.name).is_dir():
        try:
            _git(
                "submodule",
                "update",
                "--init",
                "--depth",
                "1",
                f"tests/integration/benchmark_repos/{config.name}",
                cwd=CODEBOARDING_ROOT,
            )
            if _is_valid_repo(submodule_dir, config.pinned_tag):
                logger.info("Initialised submodule at %s", submodule_dir)
                return submodule_dir
        except RuntimeError:
            logger.debug("Submodule init failed for %s, falling back to clone", config.name)

    # 2. Fall back to clone in parent_dir
    if parent_dir is None:
        parent_dir = DEFAULT_CLONE_PARENT

    repo_dir = parent_dir / config.name

    if repo_dir.exists() and (repo_dir / ".git").exists():
        if _head_matches_tag(repo_dir, config.pinned_tag):
            logger.info("Reusing existing clone at %s (already at %s)", repo_dir, config.pinned_tag)
            return repo_dir

        logger.info("Clone exists at %s but not at %s — checking out pinned tag", repo_dir, config.pinned_tag)
        try:
            _git("fetch", "origin", f"refs/tags/{config.pinned_tag}:refs/tags/{config.pinned_tag}", cwd=repo_dir)
        except RuntimeError:
            _git("fetch", "origin", cwd=repo_dir)
        _git("checkout", config.pinned_tag, cwd=repo_dir)
        _git("clean", "-fd", cwd=repo_dir)
        return repo_dir

    # Fresh clone
    parent_dir.mkdir(parents=True, exist_ok=True)
    tag = config.pinned_tag

    logger.info("Cloning %s at %s into %s ...", config.repo_url, tag, repo_dir)
    shallow = subprocess.run(
        ["git", "clone", "--depth", "1", "--branch", tag, config.repo_url, str(repo_dir)],
        capture_output=True,
        text=True,
    )
    if shallow.returncode == 0:
        return repo_dir

    # Shallow clone failed (tag name not accepted by --branch) — full clone + checkout.
    logger.info("Shallow clone failed, doing full clone + checkout for %s", config.name)
    subprocess.run(
        ["git", "clone", config.repo_url, str(repo_dir)],
        capture_output=True,
        text=True,
        check=True,
    )
    _git("checkout", tag, cwd=repo_dir)
    return repo_dir


def get_scenario_module(config: RepoConfig):
    """Import and return the scenario module for *config*."""
    if config.name == "markitdown":
        from tests.integration.incremental.scenarios_markitdown import (
            MARKITDOWN_SCENARIOS as scenarios,
            MARKITDOWN_SCENARIOS_BY_NAME as scenarios_by_name,
        )

        return scenarios, scenarios_by_name

    if config.name == "langchain":
        from tests.integration.incremental.scenarios_langchain import (
            LANGCHAIN_SCENARIOS as scenarios,
            LANGCHAIN_SCENARIOS_BY_NAME as scenarios_by_name,
        )

        return scenarios, scenarios_by_name

    if config.name == "jsoup":
        from tests.integration.incremental.scenarios_jsoup import (
            JSOUP_SCENARIOS as scenarios,
            JSOUP_SCENARIOS_BY_NAME as scenarios_by_name,
        )

        return scenarios, scenarios_by_name

    if config.name == "zustand":
        from tests.integration.incremental.scenarios_zustand import (
            ZUSTAND_SCENARIOS as scenarios,
            ZUSTAND_SCENARIOS_BY_NAME as scenarios_by_name,
        )

        return scenarios, scenarios_by_name

    # Default: deepface
    from tests.integration.incremental.scenarios import (
        SCENARIOS as scenarios,
        SCENARIOS_BY_NAME as scenarios_by_name,
    )

    return scenarios, scenarios_by_name
