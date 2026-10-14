"""Install a JavaScript workspace's dependencies when asked, so tsserver reads npm-hosted tsconfigs and their types.

Trust: no install script runs, but the repository's package-manager config does (``.npmrc``, ``.pnpmfile.cjs``).
"""

import logging
import os
import shutil
import signal
import subprocess
import tempfile
from pathlib import Path

from infra.tool_registry.paths import get_servers_dir, npm_subprocess_env

logger = logging.getLogger(__name__)

INSTALL_ENV_VAR = "CODEBOARDING_INSTALL_DEPENDENCIES"
INSTALL_TIMEOUT_SECONDS = 600  # bun was seen to hang for 49 minutes after a download failed
# The manager each lockfile implies, in precedence order, with its frozen, script-free install.
_NPM_CI = ["npm", "ci", "--ignore-scripts", "--include=dev", "--no-audit", "--no-fund"]
_INSTALLS = {
    "bun.lock": ["bun", "install", "--frozen-lockfile", "--ignore-scripts"],
    "bun.lockb": ["bun", "install", "--frozen-lockfile", "--ignore-scripts"],
    "pnpm-lock.yaml": ["pnpm", "install", "--frozen-lockfile", "--ignore-scripts"],
    "yarn.lock": ["yarn", "install", "--frozen-lockfile", "--ignore-scripts"],
    "package-lock.json": _NPM_CI,
    "npm-shrinkwrap.json": _NPM_CI,
}
# A production install leaves out devDependencies, where shared tsconfigs usually are.
_DROPPED_ENV = {"node_env", "npm_config_production", "pnpm_config_production", "npm_config_omit"}
# Also for the processes the manager starts itself, such as npm preparing a git dependency.
_CHILD_ENV = {"CI": "1", "npm_config_ignore_scripts": "true", "YARN_ENABLE_SCRIPTS": "false"}


def install_workspace_dependencies(repo: Path) -> None:
    """Install *repo*'s workspace dependencies, frozen, when the env var asks; a failure only logs and leaves none."""
    if os.environ.get(INSTALL_ENV_VAR, "").strip().lower() not in ("1", "true", "yes"):
        return
    manifest = repo / "package.json"
    if not (repo / "pnpm-workspace.yaml").is_file() and '"workspaces"' not in (
        manifest.read_text(encoding="utf-8", errors="replace") if manifest.is_file() else ""
    ):
        return
    lockfile = next((name for name in _INSTALLS if (repo / name).is_file()), None)
    if lockfile is None or (repo / "node_modules").exists():
        logger.info(f"Dependencies not installed: {'no lockfile' if lockfile is None else 'node_modules exists'}")
        return
    command = _INSTALLS[lockfile]
    if lockfile == "yarn.lock" and (repo / ".yarnrc.yml").is_file():  # Yarn 2+
        command = ["yarn", "install", "--immutable", "--mode=skip-build"]
    env = {k: v for k, v in npm_subprocess_env(get_servers_dir()).items() if k.lower() not in _DROPPED_ENV}
    binary = shutil.which(command[0], path=env.get("PATH"))
    if binary is None:
        logger.info(f"Dependencies not installed: {command[0]} is not on PATH")
        return
    created = []  # the package folders' node_modules a failed install may leave half-written
    for root, dirs, files in os.walk(repo):
        dirs[:] = [d for d in dirs if d != "node_modules" and not d.startswith(".")]
        if "package.json" in files and not (Path(root) / "node_modules").exists():
            created.append(Path(root) / "node_modules")
    with tempfile.TemporaryFile() as log:
        # Why its own session and a file, not a pipe: stopping it stops its children, and none can hold the output open.
        try:
            process = subprocess.Popen(
                [binary, *command[1:]], cwd=repo, env=env | _CHILD_ENV, stdout=log, stderr=log, start_new_session=True
            )
        except OSError:  # a broken shim, say, whose interpreter is gone
            logger.warning(f"Dependencies not installed: {binary} could not start", exc_info=True)
            return
        try:
            process.wait(timeout=INSTALL_TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired:
            pass
        finally:
            if process.poll() is None:  # timed out or interrupted
                if hasattr(os, "killpg"):
                    os.killpg(process.pid, signal.SIGKILL)
                else:
                    process.kill()
                process.wait()
        log.seek(0)
        output = [line.strip() for line in log.read().decode(errors="replace").splitlines() if line.strip()]
    if process.returncode == 0:
        logger.info(f"Dependencies installed with {command[0]}")
        return
    for folder in created:
        shutil.rmtree(folder, ignore_errors=True)
    errors = [line for line in output if "err" in line.lower()]  # npm prints its usage after the reason
    logger.warning(
        f"Dependencies failed to install with {command[0]} (exit {process.returncode}), "
        "analysing without them: " + " | ".join(errors[:5] or output[-3:])
    )
