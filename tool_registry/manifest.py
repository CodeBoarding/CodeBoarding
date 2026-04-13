"""Install-state tracking: manifest, fingerprints, locks, and config resolution."""

import importlib.metadata
import json
import logging
import os
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

from .installers import package_manager_tool_dir
from .paths import exe_suffix, get_servers_dir, platform_bin_dir, preferred_node_path
from .registry import (
    PINNED_NODE_VERSION,
    TOOL_REGISTRY,
    GitHubToolSource,
    PackageManagerToolSource,
    ToolDependency,
    ToolKind,
    UpstreamToolSource,
)

logger = logging.getLogger(__name__)


# -- Manifest + fingerprints --------------------------------------------------


def installed_version() -> str:
    try:
        return importlib.metadata.version("codeboarding")
    except importlib.metadata.PackageNotFoundError:
        return "dev"


def manifest_path() -> Path:
    return get_servers_dir() / "installed.json"


def read_manifest() -> dict:
    p = manifest_path()
    if p.exists():
        return json.loads(p.read_text())
    return {}


def npm_specs_fingerprint() -> str:
    """Deterministic fingerprint of all pinned npm package specs.

    Changes whenever an npm version pin in TOOL_REGISTRY is updated,
    causing ``needs_install()`` to trigger a reinstall.
    """
    specs: list[str] = []
    for dep in TOOL_REGISTRY:
        if dep.kind is ToolKind.NODE:
            specs.extend(sorted(dep.npm_packages))
    return ",".join(specs)


def tools_fingerprint() -> str:
    """Deterministic fingerprint of all pinned tool sources.

    Changes whenever a tool version or source in TOOL_REGISTRY is updated,
    causing ``needs_install()`` to trigger a reinstall.  Also incorporates
    ``PINNED_NODE_VERSION`` so bumping the embedded Node.js runtime invalidates
    any previously-written manifest and forces the bootstrap to re-run.
    """
    parts: list[str] = [f"node:{PINNED_NODE_VERSION}"]
    for dep in TOOL_REGISTRY:
        if dep.source:
            if isinstance(dep.source, GitHubToolSource):
                parts.append(f"{dep.key}:{dep.source.repo}:{dep.source.tag}")
            elif isinstance(dep.source, UpstreamToolSource):
                parts.append(f"{dep.key}::{dep.source.tag}-{dep.source.build}")
            elif isinstance(dep.source, PackageManagerToolSource):
                # ``install_args`` is included: flag changes (pinned version, channel) must invalidate the manifest.
                parts.append(
                    f"{dep.key}:{dep.source.manager_binary}:{dep.source.tag}:{'|'.join(dep.source.install_args)}"
                )
    return ",".join(sorted(parts))


def write_manifest() -> None:
    """Atomically persist the install manifest via tmp-file + ``os.replace``.

    ``flush + fsync`` before the rename guarantees the bytes hit disk before
    the rename becomes visible — without it, a post-rename crash can leave
    the manifest pointing at a file whose contents are only in page cache.
    """
    target = manifest_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(
        {
            "version": installed_version(),
            "npm_specs": npm_specs_fingerprint(),
            "tools": tools_fingerprint(),
        },
        indent=2,
    )
    tmp = target.with_name(target.name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(payload)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, target)


def needs_install() -> bool:
    """Return True when binaries are missing or installed by a different package version."""
    manifest = read_manifest()
    if manifest.get("version") != installed_version():
        return True
    if manifest.get("npm_specs") != npm_specs_fingerprint():
        return True
    if manifest.get("tools") != tools_fingerprint():
        return True
    return not has_required_tools(get_servers_dir())


# -- Concurrency lock ---------------------------------------------------------


def acquire_lock(lock_fd: Any) -> None:
    """Acquire an exclusive file lock, logging if we have to wait.

    Used by install.py to protect ``~/.codeboarding/servers/`` from
    concurrent writers. Blocks indefinitely — tool downloads are slow.
    """
    if sys.platform == "win32":
        # msvcrt.LK_LOCK only retries for ~10s; poll LK_NBLCK every 2s instead.
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
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            logger.info("Another instance is downloading tools, waiting...")
            print("Waiting for another instance to finish downloading tools...", flush=True, file=sys.stderr)
            fcntl.flock(lock_fd, fcntl.LOCK_EX)


# -- LSP / tool config resolution ---------------------------------------------


def build_config() -> dict[str, Any]:
    """Resolve tool config from ~/.codeboarding/servers/, falling back to system PATH.

    Returns a VSCODE_CONFIG-shaped dict with command paths made absolute.
    """
    servers = get_servers_dir()
    config = resolve_config(servers)
    path_config = resolve_config_from_path()
    # Fall back to PATH only if no command entry is absolute. Windows Node
    # tools look like [node, /abs/entry.mjs, ...] — cmd[1] being absolute counts.
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


def package_manager_tool_path(base_dir: Path, dep: ToolDependency) -> Path | None:
    """Absolute path to a PACKAGE_MANAGER tool's installed binary.

    Returns ``None`` on hosts where ``platform_bin_dir`` has no layout —
    the native installer path already skips unsupported OSes, so the
    summary and config resolution need a matching signal rather than a
    hard crash.
    """
    try:
        return package_manager_tool_dir(base_dir, dep) / f"{dep.binary_name}{exe_suffix()}"
    except RuntimeError:
        return None


def resolve_config(base_dir: Path) -> dict[str, Any]:
    """Scan base_dir for installed tools and return a config dict.

    The returned dict has the same shape as VSCODE_CONFIG ("lsp_servers" + "tools")
    with command paths resolved to absolute paths under base_dir.
    """
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

        elif dep.kind is ToolKind.PACKAGE_MANAGER:
            binary_path = package_manager_tool_path(base_dir, dep)
            if binary_path is not None and binary_path.exists():
                cmd = cast(list[str], config[dep.config_section][dep.key]["command"])
                cmd[0] = str(binary_path)

        elif dep.kind is ToolKind.NODE:
            binary_path = base_dir / "node_modules" / ".bin" / f"{dep.binary_name}{node_ext}"
            if binary_path.exists():
                cmd = cast(list[str], config[dep.config_section][dep.key]["command"])
                if dep.js_entry_file:
                    # Prefer [node, <abs/entry.mjs>] so frozen wrapper binaries
                    # can use their embedded Node runtime.
                    js_entry = find_runnable(str(base_dir), dep.js_entry_file, dep.js_entry_parent or dep.binary_name)
                    node_path = preferred_node_path(base_dir)
                    if js_entry and node_path:
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
    config = deepcopy(VSCODE_CONFIG)

    for dep in TOOL_REGISTRY:
        path = None
        if dep.kind in (ToolKind.NATIVE, ToolKind.NODE, ToolKind.PACKAGE_MANAGER):
            path = shutil.which(dep.binary_name)
        if path:
            cmd = cast(list[str], config[dep.config_section][dep.key]["command"])
            if platform.system() == "Windows" and dep.kind is ToolKind.NODE and dep.js_entry_file:
                # Bypass .cmd wrappers on Windows — they cause pipe buffering issues.
                bin_dir = str(Path(path).parent.parent)  # .bin -> node_modules/..
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
    """Return True when every ``TOOL_REGISTRY`` artifact is present on disk.

    Validation rules are kept in sync with ``resolve_config``:
    NATIVE -> ``platform_bin_dir/<binary><exe>`` exists;
    NODE -> ``find_runnable`` locates ``js_entry_file`` (``.bin/`` wrapper is
    skipped because Windows AV strips it first, and the resolver bypasses it too);
    ARCHIVE -> ``bin/<archive_subdir>/plugins/`` exists.
    """
    if not base_dir.exists():
        return False

    for dep in TOOL_REGISTRY:
        if dep.kind is ToolKind.NATIVE:
            # Skip the check when the installer would also skip the download,
            # otherwise ``needs_install`` loops forever on unsupported hosts.
            if not dep.is_available_on_host():
                logger.info("has_required_tools: %s unavailable on this host; skipping check", dep.key)
                continue
            binary_path = platform_bin_dir(base_dir) / f"{dep.binary_name}{exe_suffix()}"
            if not binary_path.exists():
                logger.info("has_required_tools: %s missing at %s", dep.key, binary_path)
                return False

        elif dep.kind is ToolKind.PACKAGE_MANAGER:
            # Skip when the package manager itself is missing — the installer
            # also skips, and the adapter raises a meaningful error at
            # analysis time. Without this skip, ``needs_install`` loops on
            # machines without the user-provided toolchain.
            source = dep.source
            if isinstance(source, PackageManagerToolSource) and not shutil.which(source.manager_binary):
                logger.info(
                    "has_required_tools: %s unavailable (%s missing); skipping check",
                    dep.key,
                    source.manager_binary,
                )
                continue
            binary_path = package_manager_tool_path(base_dir, dep)
            if binary_path is None:
                logger.info("has_required_tools: %s unsupported on this host; skipping check", dep.key)
                continue
            if not binary_path.exists():
                logger.info("has_required_tools: %s missing at %s", dep.key, binary_path)
                return False

        elif dep.kind is ToolKind.NODE:
            # A dep without js_entry_file is unverifiable here — treat as present.
            if not dep.js_entry_file:
                continue
            js_entry = find_runnable(str(base_dir), dep.js_entry_file, dep.js_entry_parent or dep.binary_name)
            if not js_entry:
                logger.info(
                    "has_required_tools: %s JS entry %s not found under %s",
                    dep.key,
                    dep.js_entry_file,
                    base_dir / "node_modules",
                )
                return False

        elif dep.kind is ToolKind.ARCHIVE and dep.archive_subdir:
            archive_dir = base_dir / "bin" / dep.archive_subdir
            if not (archive_dir.is_dir() and (archive_dir / "plugins").is_dir()):
                logger.info(
                    "has_required_tools: %s archive missing or incomplete at %s",
                    dep.key,
                    archive_dir,
                )
                return False

    return True
