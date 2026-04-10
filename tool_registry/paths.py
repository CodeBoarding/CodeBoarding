"""Filesystem paths and Node.js runtime resolution."""

import functools
import logging
import os
import platform
import shutil
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)


# Derived from the strictest engines.node across pinned npm packages
# (typescript-language-server@4.3.4 requires >=18). Bump when pins change.
MINIMUM_NODE_MAJOR_VERSION = 18


_PLATFORM_BIN_SUBDIR = {
    "windows": "win",
    "darwin": "macos",
    "linux": "linux",
}


# -- Platform helpers ---------------------------------------------------------


def exe_suffix() -> str:
    """Return the platform-specific executable suffix ('.exe' on Windows, '' elsewhere)."""
    return ".exe" if platform.system() == "Windows" else ""


def platform_bin_dir(base: Path) -> Path:
    """Return the platform-specific binary directory under base (e.g. base/bin/macos)."""
    system = platform.system().lower()
    subdir = _PLATFORM_BIN_SUBDIR.get(system)
    if subdir is None:
        raise RuntimeError(f"Unsupported platform: {system}")
    return base / "bin" / subdir


# -- User data directory ------------------------------------------------------


def user_data_dir() -> Path:
    """Return the user-level persistent storage directory (~/.codeboarding)."""
    return Path.home() / ".codeboarding"


def get_servers_dir() -> Path:
    """Return the directory where language server binaries are installed."""
    return user_data_dir() / "servers"


# -- Embedded nodeenv layout --------------------------------------------------


def nodeenv_root_dir(base_dir: Path) -> Path:
    """Return the standalone nodeenv directory under a tool install root."""
    return base_dir / "nodeenv"


def nodeenv_bin_dir(base_dir: Path) -> Path:
    """Return the bin/Scripts directory for a standalone nodeenv install."""
    scripts_dir = "Scripts" if platform.system() == "Windows" else "bin"
    return nodeenv_root_dir(base_dir) / scripts_dir


def embedded_node_path(base_dir: Path) -> str | None:
    """Return the node binary from a standalone nodeenv install, if present."""
    suffix = ".exe" if platform.system() == "Windows" else ""
    node_path = nodeenv_bin_dir(base_dir) / f"node{suffix}"
    return str(node_path) if node_path.exists() else None


def embedded_npm_path(base_dir: Path) -> str | None:
    """Return the npm binary from a standalone nodeenv install, if present."""
    suffix = ".cmd" if platform.system() == "Windows" else ""
    npm_path = nodeenv_bin_dir(base_dir) / f"npm{suffix}"
    return str(npm_path) if npm_path.exists() else None


def embedded_npm_cli_path(base_dir: Path) -> str | None:
    """Return a bootstrapped npm CLI JS entrypoint, if present."""
    npm_cli = base_dir / "npm" / "package" / "bin" / "npm-cli.js"
    return str(npm_cli) if npm_cli.exists() else None


# -- Node.js version probing --------------------------------------------------


@functools.lru_cache(maxsize=8)
def node_version_tuple(node_path: str) -> tuple[int, int, int] | None:
    """Probe a Node.js binary for its version.

    Returns ``(major, minor, patch)`` on success, ``None`` for anything
    unrunnable (missing, hangs, crashes, unparseable output). Never raises.

    Cached per-path because ``build_config()`` is invoked many times per
    analysis and would otherwise re-spawn ``node --version`` on each call.
    Tests must call ``cache_clear()`` in ``setUp`` to avoid cross-test leaks.
    """
    if not node_path:
        return None

    # Guard against stale CODEBOARDING_NODE_PATH pointing at a deleted file —
    # otherwise subprocess.Popen downstream would raise FileNotFoundError.
    if not Path(node_path).exists():
        return None

    # ELECTRON_RUN_AS_NODE=1 lets VS Code's Electron binary behave as Node.
    env = dict(os.environ)
    env["ELECTRON_RUN_AS_NODE"] = "1"

    try:
        result = subprocess.run(
            [node_path, "--version"],
            capture_output=True,
            text=True,
            timeout=5,  # catch hangs on network FS / AV scans (cold start ~50ms)
            env=env,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None

    if result.returncode != 0:
        return None

    # Node prints ``v20.18.1\n``.
    raw = result.stdout.strip().lstrip("v")
    parts = raw.split(".")
    if len(parts) < 3:
        return None

    try:
        return (int(parts[0]), int(parts[1]), int(parts[2]))
    except ValueError:
        return None


def node_is_acceptable(node_path: str | None) -> bool:
    """Return True if ``node_path`` is runnable and meets ``MINIMUM_NODE_MAJOR_VERSION``."""
    if not node_path:
        return False

    version = node_version_tuple(node_path)
    if version is None:
        logger.info("Node.js candidate %s is not runnable; skipping", node_path)
        return False

    if version[0] < MINIMUM_NODE_MAJOR_VERSION:
        logger.info(
            "Node.js candidate %s is v%d.%d.%d; minimum required is v%d. Skipping.",
            node_path,
            version[0],
            version[1],
            version[2],
            MINIMUM_NODE_MAJOR_VERSION,
        )
        return False

    return True


# -- Node.js / npm runtime resolution -----------------------------------------


def preferred_node_path(base_dir: Path) -> str | None:
    """Return the first acceptable Node binary from: CODEBOARDING_NODE_PATH,
    embedded nodeenv, system PATH. Unacceptable candidates fall through."""
    candidate = os.environ.get("CODEBOARDING_NODE_PATH")
    if node_is_acceptable(candidate):
        return candidate

    candidate = embedded_node_path(base_dir)
    if node_is_acceptable(candidate):
        return candidate

    candidate = shutil.which("node")
    if node_is_acceptable(candidate):
        return candidate

    return None


def sibling_npm_path(node_path: str | None) -> str | None:
    """Return an npm executable located next to the provided node binary, if present."""
    if not node_path:
        return None

    node_dir = Path(node_path).parent
    candidates = ["npm.cmd", "npm.exe", "npm"] if platform.system() == "Windows" else ["npm"]
    for candidate_name in candidates:
        candidate = node_dir / candidate_name
        if candidate.exists():
            return str(candidate)
    return None


def preferred_npm_command(base_dir: Path) -> list[str] | None:
    """Return the preferred command prefix for invoking npm.

    The sibling-npm branch only trusts ``CODEBOARDING_NODE_PATH`` when that
    Node is acceptable — otherwise LSPs would run against one Node but be
    installed by the npm of another.
    """
    if npm_path := embedded_npm_path(base_dir):
        return [npm_path]
    env_node = os.environ.get("CODEBOARDING_NODE_PATH")
    if node_is_acceptable(env_node):
        if npm_path := sibling_npm_path(env_node):
            return [npm_path]
    if node_path := preferred_node_path(base_dir):
        if npm_cli_path := embedded_npm_cli_path(base_dir):
            return [node_path, npm_cli_path]
    if npm_path := shutil.which("npm"):
        return [npm_path]
    return None


def npm_subprocess_env(base_dir: Path) -> dict[str, str]:
    """Return env for npm subprocess calls: sets ELECTRON_RUN_AS_NODE and
    prepends the node binary's directory to PATH so lifecycle scripts can find ``node``."""
    env = dict(os.environ)
    node = preferred_node_path(base_dir)
    if node:
        env["ELECTRON_RUN_AS_NODE"] = "1"
        node_dir = str(Path(node).parent)
        env["PATH"] = node_dir + os.pathsep + env.get("PATH", "")
    return env


def ensure_node_on_path(command: list[str], extra_env: dict[str, str]) -> None:
    """Prepend the node binary's directory to ``extra_env['PATH']`` when
    ``command[0]`` is an absolute path to a ``node`` / ``node.exe`` binary.

    Node-based LSPs (pyright, tsserver) spawn child ``node`` processes by
    name; without this the embedded-Node bootstrap works for the LSP itself
    but child spawns fail with ENOENT on Node-less machines. Constructs the
    final PATH explicitly because LSPClient.start() does ``env.update(extra_env)``
    which replaces rather than merges. Skips Electron binaries — those are
    handled via ``ELECTRON_RUN_AS_NODE``.
    """
    if not command:
        return
    first_path = Path(command[0])
    if not first_path.is_absolute():
        return
    if first_path.name.lower() not in ("node", "node.exe"):
        return
    node_dir = str(first_path.parent)
    if not node_dir:
        return
    # Baseline: adapter's PATH if set, else the process PATH that
    # LSPClient.start() will otherwise inherit via os.environ.copy().
    baseline = extra_env.get("PATH", os.environ.get("PATH", ""))
    if node_dir in baseline.split(os.pathsep):
        # Already on PATH — copy baseline into extra_env so env.update
        # doesn't drop the PATH key we intended.
        extra_env["PATH"] = baseline
        return
    extra_env["PATH"] = node_dir + os.pathsep + baseline if baseline else node_dir
