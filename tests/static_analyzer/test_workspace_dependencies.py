"""The opt-in workspace install, run against fake package managers on PATH that record how they were called."""

import json
import logging
import os
import sys
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import static_analyzer
from static_analyzer import workspace_dependencies
from static_analyzer.workspace_dependencies import INSTALL_ENV_VAR, install_workspace_dependencies

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="the fake package managers are shell scripts")

_LINKS = 'mkdir -p node_modules packages/a/node_modules && echo "$(basename "$0") $*" > "$CALLS"'


@pytest.mark.parametrize(
    "lockfile, expected",
    [
        ("bun.lock", "bun install --frozen-lockfile --ignore-scripts"),
        ("pnpm-lock.yaml", "pnpm install --frozen-lockfile --ignore-scripts"),
        ("yarn.lock", "yarn install --frozen-lockfile --ignore-scripts"),
        ("package-lock.json", "npm ci --ignore-scripts --include=dev --no-audit --no-fund"),
    ],
)
def test_the_lockfile_picks_a_frozen_install_without_scripts(tmp_path: Path, monkeypatch, lockfile: str, expected: str):
    repo, calls = _workspace(tmp_path, monkeypatch, lockfile, _LINKS)

    install_workspace_dependencies(repo)

    assert calls.read_text().strip() == expected
    assert (repo / "node_modules").is_dir()


def test_yarn_berry_installs_immutable_without_builds(tmp_path: Path, monkeypatch):
    repo, calls = _workspace(tmp_path, monkeypatch, "yarn.lock", _LINKS)
    (repo / ".yarnrc.yml").write_text("nodeLinker: node-modules\n")

    install_workspace_dependencies(repo)

    assert calls.read_text().strip() == "yarn install --immutable --mode=skip-build"


def test_a_production_environment_still_installs_dev_dependencies(tmp_path: Path, monkeypatch):
    repo, calls = _workspace(
        tmp_path,
        monkeypatch,
        "package-lock.json",
        'echo "${NODE_ENV}|${npm_config_omit}|${npm_config_ignore_scripts}" > "$CALLS"',
    )
    monkeypatch.setenv("NODE_ENV", "production")
    monkeypatch.setenv("npm_config_omit", "dev")

    install_workspace_dependencies(repo)

    assert calls.read_text().strip() == "||true"


@pytest.mark.parametrize("case", ["not asked", "no workspace", "no lockfile", "installed", "manager missing"])
def test_nothing_runs_when_it_has_nothing_to_do(tmp_path: Path, monkeypatch, case: str):
    repo, calls = _workspace(tmp_path, monkeypatch, "pnpm-lock.yaml", _LINKS)
    if case == "not asked":
        monkeypatch.delenv(INSTALL_ENV_VAR)
    elif case == "no workspace":
        (repo / "package.json").write_text(json.dumps({"name": "root"}))
    elif case == "no lockfile":
        (repo / "pnpm-lock.yaml").unlink()
    elif case == "installed":
        (repo / "node_modules").mkdir()
    else:
        (repo.parent / "bin" / "pnpm").unlink()

    install_workspace_dependencies(repo)

    assert not calls.exists()


def test_a_failed_install_removes_only_the_node_modules_it_created(tmp_path: Path, monkeypatch, caplog):
    repo, _ = _workspace(
        tmp_path, monkeypatch, "pnpm-lock.yaml", f"{_LINKS}; echo 'ERR_PNPM_OUTDATED_LOCKFILE'; exit 1"
    )
    (repo / "packages/b/node_modules").mkdir()  # a developer's, from before the run

    with caplog.at_level(logging.WARNING):
        install_workspace_dependencies(repo)

    assert not (repo / "node_modules").exists() and not (repo / "packages/a/node_modules").exists()
    assert (repo / "packages/b/node_modules").is_dir()
    assert "ERR_PNPM_OUTDATED_LOCKFILE" in caplog.text


def test_a_package_manager_that_cannot_start_is_a_failed_install(tmp_path: Path, monkeypatch, caplog):
    repo, _ = _workspace(tmp_path, monkeypatch, "pnpm-lock.yaml", "")
    (tmp_path / "bin" / "pnpm").write_text("#!/nonexistent/interpreter\n")

    with caplog.at_level(logging.WARNING):
        install_workspace_dependencies(repo)

    assert "could not start" in caplog.text
    assert not (repo / "node_modules").exists()


def test_a_hanging_install_is_stopped_with_its_children(tmp_path: Path, monkeypatch):
    repo, _ = _workspace(tmp_path, monkeypatch, "bun.lock", f"{_LINKS}; sleep 30 & echo $! > child; wait")
    monkeypatch.setattr(workspace_dependencies, "INSTALL_TIMEOUT_SECONDS", 1)

    started = time.monotonic()
    install_workspace_dependencies(repo)

    assert time.monotonic() - started < 10
    child = int((repo / "child").read_text())
    assert not _alive_after(5, child)
    assert not (repo / "node_modules").exists()


def test_the_cli_installs_before_the_analyzer_starts(tmp_path: Path):
    calls = MagicMock()
    with (
        patch.object(static_analyzer, "install_workspace_dependencies", calls.install),
        patch.object(static_analyzer, "StaticAnalyzer", calls.analyzer),
    ):
        static_analyzer.get_static_analysis(tmp_path, cache_dir=tmp_path)

    assert [name for name, *_ in calls.mock_calls[:2]] == ["install", "analyzer"]


def _alive_after(seconds: float, pid: int) -> bool:
    """Whether *pid* still runs after *seconds*; the orphan may take a moment to be reaped."""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        time.sleep(0.1)
    return True


def _workspace(tmp_path: Path, monkeypatch, lockfile: str, script: str) -> tuple[Path, Path]:
    """A two-member workspace with *lockfile*, and every package manager on PATH running *script*."""
    repo, bin_dir = tmp_path / "repo", tmp_path / "bin"
    for member in ("a", "b"):
        (repo / "packages" / member).mkdir(parents=True)
        (repo / "packages" / member / "package.json").write_text(json.dumps({"name": f"@acme/{member}"}))
    (repo / "package.json").write_text(json.dumps({"name": "root", "workspaces": ["packages/*"]}))
    (repo / lockfile).write_text("")
    bin_dir.mkdir()
    for manager in ("bun", "pnpm", "yarn", "npm"):
        (bin_dir / manager).write_text(f"#!/bin/sh\n{script}\n")
        (bin_dir / manager).chmod(0o755)
    calls = tmp_path / "calls"
    monkeypatch.setenv(INSTALL_ENV_VAR, "1")
    monkeypatch.setenv("CALLS", str(calls))
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}/bin{os.pathsep}/usr/bin")
    monkeypatch.setattr(workspace_dependencies, "npm_subprocess_env", lambda _: dict(os.environ))
    return repo, calls
