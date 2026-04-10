"""Filesystem paths and Node.js runtime resolution.

This module is the single source of truth for "where does X live on disk"
and "which Node binary should we use." It is consumed by both the manifest
layer (which needs servers_dir/platform_bin_dir) and the installers layer
(which needs all of it, plus the Node resolution chain to decide which npm
to invoke).

The Node resolution logic lives here rather than in a separate ``node_runtime``
module because it is tightly coupled to the path helpers — ``preferred_node_path``
walks a candidate chain that includes ``embedded_node_path``, and splitting
them would force a circular dependency or an awkward re-import.
"""

import logging
import os
import platform
import shutil
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)


# Minimum Node.js major version required to run the pinned language servers.
# Derived from the strictest ``engines.node`` constraint across our pinned
# npm packages:
#
#   - typescript-language-server@4.3.4: ``>=18``
#   - pyright@1.1.400:                  ``>=14``
#   - intelephense@1.16.5:              (no constraint declared)
#
# If we bump any pinned npm package and its minimum increases, update this
# value deliberately — the version probe in ``_node_version_tuple()`` will
# start rejecting previously-accepted system Nodes, and users on older LTS
# will see the embedded runtime take over automatically.
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


def _node_version_tuple(node_path: str) -> tuple[int, int, int] | None:
    """Probe a Node.js binary for its version.

    Returns a ``(major, minor, patch)`` tuple on success, ``None`` when the
    path is not a runnable Node binary (doesn't exist, hangs, crashes, or
    prints unparseable output).  Intentionally tolerant: a None return is
    interpreted by ``preferred_node_path()`` as "skip this candidate and try
    the next one," so this must never raise.

    Sets ``ELECTRON_RUN_AS_NODE=1`` in the subprocess env so VS Code's
    Electron binary — which is the most common ``CODEBOARDING_NODE_PATH``
    target in practice — behaves as plain Node instead of launching an
    editor window.

    The 5-second timeout exists to catch two real failure modes: a binary
    that hangs on a network-mounted filesystem waiting for backing storage,
    and an antivirus tool that stalls the first execution while it scans
    the file.  Longer than typical Node startup (~50ms cold) by two orders
    of magnitude — any legitimate Node binary will finish well before this.
    """
    if not node_path:
        return None

    # Reject paths that clearly don't exist before spending a subprocess.
    # This is what fixes V4 (``CODEBOARDING_NODE_PATH`` pointing at a deleted
    # file): without this guard ``preferred_node_path`` would hand that path
    # back to callers, and ``subprocess.Popen`` downstream would raise
    # ``FileNotFoundError: 'node'`` — the exact symptom this whole change
    # set was written to prevent, surfaced through a different code path.
    if not Path(node_path).exists():
        return None

    env = dict(os.environ)
    env["ELECTRON_RUN_AS_NODE"] = "1"

    try:
        result = subprocess.run(
            [node_path, "--version"],
            capture_output=True,
            text=True,
            timeout=5,
            env=env,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        # OSError covers permission-denied, not-executable, etc.
        # TimeoutExpired covers hangs (corrupt binary, network FS stall).
        return None

    if result.returncode != 0:
        return None

    # Node prints ``v20.18.1\n`` — strip the leading 'v' and any whitespace.
    raw = result.stdout.strip()
    if raw.startswith("v"):
        raw = raw[1:]

    parts = raw.split(".")
    if len(parts) < 3:
        return None

    try:
        return (int(parts[0]), int(parts[1]), int(parts[2]))
    except ValueError:
        return None


def _node_is_acceptable(node_path: str | None) -> bool:
    """Return True if ``node_path`` resolves to a Node binary that meets
    ``MINIMUM_NODE_MAJOR_VERSION``.

    Wrapper around ``_node_version_tuple`` that applies the minimum-version
    policy in one place so callers (``preferred_node_path``,
    ``preferred_npm_command``) stay consistent about which candidates are
    usable.  Logs at INFO when rejecting a candidate so operators who set
    ``CODEBOARDING_NODE_PATH`` intentionally can see why it was ignored
    without getting a noisy warning every call.
    """
    if not node_path:
        return False

    version = _node_version_tuple(node_path)
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
    """Return the preferred Node.js binary for running JS-based language servers.

    Walks the candidate resolution chain in order:

        1. ``CODEBOARDING_NODE_PATH`` environment variable (VS Code's Electron
           binary in the common case)
        2. Embedded Node.js from a previous ``install_embedded_node`` bootstrap
        3. ``node`` on the system PATH

    Each candidate is validated via ``_node_is_acceptable()`` — which rejects
    missing files (the original ``CODEBOARDING_NODE_PATH=/nonexistent`` bug),
    unrunnable binaries, and Nodes older than ``MINIMUM_NODE_MAJOR_VERSION``.
    Unusable candidates fall through to the next one, so a user with Node 16
    on PATH and no ``CODEBOARDING_NODE_PATH`` will transparently end up using
    the embedded runtime — the same recovery path as a user with no Node at all.
    """
    candidate = os.environ.get("CODEBOARDING_NODE_PATH")
    if _node_is_acceptable(candidate):
        return candidate

    candidate = embedded_node_path(base_dir)
    if _node_is_acceptable(candidate):
        return candidate

    candidate = shutil.which("node")
    if _node_is_acceptable(candidate):
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

    The sibling-npm branch only trusts ``CODEBOARDING_NODE_PATH`` if that
    Node passes ``_node_is_acceptable`` — otherwise we would use the npm
    belonging to a too-old Node that ``preferred_node_path`` has already
    rejected, producing inconsistent state where the LSPs run against one
    Node and were installed by the npm of another.
    """
    if npm_path := embedded_npm_path(base_dir):
        return [npm_path]
    env_node = os.environ.get("CODEBOARDING_NODE_PATH")
    if _node_is_acceptable(env_node):
        if npm_path := sibling_npm_path(env_node):
            return [npm_path]
    if node_path := preferred_node_path(base_dir):
        if npm_cli_path := embedded_npm_cli_path(base_dir):
            return [node_path, npm_cli_path]
    if npm_path := shutil.which("npm"):
        return [npm_path]
    return None


def npm_subprocess_env(base_dir: Path) -> dict[str, str]:
    """Return environment variables needed for npm subprocess calls.

    When the Node.js runtime is VS Code's Electron binary, we must set
    ELECTRON_RUN_AS_NODE=1 so it behaves as plain Node.  We also put the
    node binary's directory on PATH so npm's internal child processes
    (lifecycle scripts, etc.) can find ``node``.
    """
    env = dict(os.environ)
    node = preferred_node_path(base_dir)
    if node:
        env["ELECTRON_RUN_AS_NODE"] = "1"
        node_dir = str(Path(node).parent)
        env["PATH"] = node_dir + os.pathsep + env.get("PATH", "")
    return env
