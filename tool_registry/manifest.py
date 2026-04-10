"""Install-state tracking: manifest, fingerprints, locks, and config resolution.

This module is the "what's installed and is it still current?" layer.  It
owns:

    * The install manifest file at ``<servers_dir>/installed.json`` and the
      fingerprint functions that decide whether a reinstall is needed.
    * The cross-process file lock that protects concurrent installs from
      stomping on each other.
    * The LSP/tool config builder that walks ``<servers_dir>/`` and produces
      a fully-resolved command dict for each language server.

It sits between ``paths`` (where things live on disk) and ``installers``
(what to do when the manifest says we need to reinstall), so it only
imports from ``paths`` and from the package root (for registry data).
"""

import importlib.metadata
import json
import logging
import platform
import shutil
import sys
import time
from copy import deepcopy
from pathlib import Path
from typing import Any, cast

if sys.platform == "win32":
    import msvcrt
else:
    import fcntl

from vscode_constants import VSCODE_CONFIG, find_runnable

from .paths import exe_suffix, get_servers_dir, platform_bin_dir, preferred_node_path

logger = logging.getLogger(__name__)


# -- Manifest + fingerprints --------------------------------------------------


def _installed_version() -> str:
    try:
        return importlib.metadata.version("codeboarding")
    except importlib.metadata.PackageNotFoundError:
        return "dev"


def _manifest_path() -> Path:
    return get_servers_dir() / "installed.json"


def _read_manifest() -> dict:
    p = _manifest_path()
    if p.exists():
        return json.loads(p.read_text())
    return {}


def _npm_specs_fingerprint() -> str:
    """Deterministic fingerprint of all pinned npm package specs.

    Changes whenever an npm version pin in TOOL_REGISTRY is updated,
    causing ``needs_install()`` to trigger a reinstall.
    """
    # Local import to avoid a circular dependency: manifest is imported by
    # ``tool_registry/__init__.py`` at the bottom, which means at module
    # load time the ``TOOL_REGISTRY`` attribute of the package is already
    # set.  We resolve it lazily so the import order stays correct.
    from . import TOOL_REGISTRY, ToolKind

    specs: list[str] = []
    for dep in TOOL_REGISTRY:
        if dep.kind is ToolKind.NODE:
            specs.extend(sorted(dep.npm_packages))
    return ",".join(specs)


def _tools_fingerprint() -> str:
    """Deterministic fingerprint of all pinned tool sources.

    Changes whenever a tool version or source in TOOL_REGISTRY is updated,
    causing ``needs_install()`` to trigger a reinstall.  Also incorporates
    ``PINNED_NODE_VERSION`` so bumping the embedded Node.js runtime invalidates
    any previously-written manifest and forces the bootstrap to re-run.
    """
    from . import PINNED_NODE_VERSION, TOOL_REGISTRY, GitHubToolSource, UpstreamToolSource

    parts: list[str] = [f"node:{PINNED_NODE_VERSION}"]
    for dep in TOOL_REGISTRY:
        if dep.source:
            if isinstance(dep.source, GitHubToolSource):
                parts.append(f"{dep.key}:{dep.source.repo}:{dep.source.tag}")
            elif isinstance(dep.source, UpstreamToolSource):
                parts.append(f"{dep.key}::{dep.source.tag}-{dep.source.build}")
    return ",".join(sorted(parts))


def write_manifest() -> None:
    p = _manifest_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        json.dumps(
            {
                "version": _installed_version(),
                "npm_specs": _npm_specs_fingerprint(),
                "tools": _tools_fingerprint(),
            },
            indent=2,
        )
    )


def needs_install() -> bool:
    """Return True when binaries are missing or installed by a different package version."""
    manifest = _read_manifest()
    if manifest.get("version") != _installed_version():
        return True
    if manifest.get("npm_specs") != _npm_specs_fingerprint():
        return True
    if manifest.get("tools") != _tools_fingerprint():
        return True
    return not has_required_tools(get_servers_dir())


# -- Concurrency lock ---------------------------------------------------------


def acquire_lock(lock_fd: Any) -> None:
    """Acquire an exclusive file lock, logging if we have to wait.

    Public across the install layer: ``install.py`` calls this from both
    ``ensure_tools`` and ``main()`` to protect the ``~/.codeboarding/servers/``
    directory from concurrent writers.  The name does not have an underscore
    prefix because it is part of the cross-module contract with install.py,
    not an implementation detail of this module.
    """
    if sys.platform == "win32":
        # msvcrt.LK_LOCK only retries for ~10 s which is too short for tool
        # downloads.  Poll with LK_NBLCK every 2 s instead — no hard timeout.
        try:
            msvcrt.locking(lock_fd.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            logger.info("Another instance is downloading tools, waiting...")
            print("Waiting for another instance to finish downloading tools...", flush=True, file=sys.stderr)
            while True:
                time.sleep(2)
                try:
                    msvcrt.locking(lock_fd.fileno(), msvcrt.LK_NBLCK, 1)
                    break
                except OSError:
                    continue
    else:
        # fcntl.LOCK_EX blocks indefinitely — exactly what we want.
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            logger.info("Another instance is downloading tools, waiting...")
            print("Waiting for another instance to finish downloading tools...", flush=True, file=sys.stderr)
            fcntl.flock(lock_fd, fcntl.LOCK_EX)


# -- LSP / tool config resolution ---------------------------------------------


def build_config() -> dict[str, Any]:
    """Build the tool config dict from ~/.codeboarding/servers/, falling back to system PATH.

    The returned dict has the same shape as VSCODE_CONFIG ("lsp_servers" + "tools")
    with command paths resolved to absolute paths wherever binaries are found.
    """
    servers = get_servers_dir()
    config = resolve_config(servers)
    path_config = resolve_config_from_path()
    # For any entry still pointing to a bare name (not found in servers dir), try system PATH.
    # Skip entries where resolve_config() already resolved the tool (e.g. on Windows, Node tools
    # use [node, /absolute/path/to/entry.mjs, ...] — cmd[0] is "node" but cmd[1] is absolute).
    for section in ("lsp_servers", "tools"):
        for key, entry in config[section].items():
            cmd = entry.get("command", [])
            if not cmd:
                continue
            has_absolute = any(Path(c).is_absolute() for c in cmd)
            if not has_absolute:
                path_cmd = path_config[section][key].get("command", [])
                if path_cmd and Path(path_cmd[0]).is_absolute():
                    entry["command"] = list(path_cmd)
    return config


def resolve_config(base_dir: Path) -> dict[str, Any]:
    """Scan base_dir for installed tools and return a config dict.

    The returned dict has the same shape as VSCODE_CONFIG ("lsp_servers" + "tools")
    with command paths resolved to absolute paths under base_dir.
    """
    from . import TOOL_REGISTRY, ToolKind

    config = deepcopy(VSCODE_CONFIG)
    bin_dir = platform_bin_dir(base_dir)
    native_ext = exe_suffix()
    node_ext = ".cmd" if platform.system() == "Windows" else ""

    for dep in TOOL_REGISTRY:
        if dep.kind is ToolKind.NATIVE:
            binary_path = bin_dir / f"{dep.binary_name}{native_ext}"
            if binary_path.exists():
                cmd = cast(list[str], config[dep.config_section][dep.key]["command"])
                cmd[0] = str(binary_path)

        elif dep.kind is ToolKind.NODE:
            binary_path = base_dir / "node_modules" / ".bin" / f"{dep.binary_name}{node_ext}"
            if binary_path.exists():
                cmd = cast(list[str], config[dep.config_section][dep.key]["command"])
                if dep.js_entry_file:
                    js_entry = find_runnable(str(base_dir), dep.js_entry_file, dep.js_entry_parent or dep.binary_name)
                    node_path = preferred_node_path(base_dir)
                    if js_entry and node_path:
                        # Run the JS entry file with an explicit Node.js path so frozen
                        # wrapper binaries can use their bundled/embedded Node runtime too.
                        cmd[0] = js_entry
                        cmd.insert(0, node_path)
                    else:
                        cmd[0] = str(binary_path)
                else:
                    cmd[0] = str(binary_path)

        elif dep.kind is ToolKind.ARCHIVE and dep.archive_subdir:
            archive_dir = base_dir / "bin" / dep.archive_subdir
            if archive_dir.is_dir() and (archive_dir / "plugins").is_dir():
                config[dep.config_section][dep.key]["jdtls_root"] = str(archive_dir)

    return config


def resolve_config_from_path() -> dict[str, Any]:
    """Discover tools on the system PATH and return a config dict."""
    from . import TOOL_REGISTRY, ToolKind

    config = deepcopy(VSCODE_CONFIG)

    for dep in TOOL_REGISTRY:
        path = None
        if dep.kind in (ToolKind.NATIVE, ToolKind.NODE):
            path = shutil.which(dep.binary_name)
        if path:
            cmd = cast(list[str], config[dep.config_section][dep.key]["command"])
            if platform.system() == "Windows" and dep.kind is ToolKind.NODE and dep.js_entry_file:
                # On Windows, bypass .cmd wrappers found on PATH — same rationale
                # as resolve_config(): .cmd wrappers cause pipe buffering issues.
                # Walk up from the resolved binary to find the JS entry point.
                bin_dir = str(Path(path).parent.parent)  # .../node_modules/.bin -> .../node_modules/..
                js_entry = find_runnable(bin_dir, dep.js_entry_file, dep.js_entry_parent or dep.binary_name)
                node = preferred_node_path(get_servers_dir())
                if js_entry and node:
                    cmd[0] = js_entry
                    cmd.insert(0, node)
                else:
                    cmd[0] = path
            else:
                cmd[0] = path

    return config


def has_required_tools(base_dir: Path) -> bool:
    """Check if the minimum required tools (tokei) are installed."""
    if not base_dir.exists():
        return False
    bin_dir = platform_bin_dir(base_dir)
    tokei = bin_dir / f"tokei{exe_suffix()}"
    return tokei.exists()
