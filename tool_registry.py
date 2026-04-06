"""Declarative registry of external tool dependencies.

This is the single source of truth for what tools CodeBoarding needs,
how to install them, and how to locate them. Both Core's install.py
and the wrapper's tool_config.py delegate to this module.

Adding a new language/tool:
    1. Add a ToolDependency entry to TOOL_REGISTRY below
    2. Add the corresponding config entry to VSCODE_CONFIG in vscode_constants.py
    3. Add to the Language enum in static_analyzer/constants.py
    That's it — install, resolve, and wrapper pick it up automatically.
"""

import hashlib
import importlib.metadata
import json
import logging
import os
import platform
import shutil
import subprocess
import sys
import tarfile
import time
from collections.abc import Callable
from copy import deepcopy
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any, cast

if sys.platform == "win32":
    import msvcrt
else:
    import fcntl

import requests
from vscode_constants import VSCODE_CONFIG, find_runnable

logger = logging.getLogger(__name__)

# Callback type for reporting download progress: (tool_name, current_step, total_steps)
ProgressCallback = Callable[[str, int, int], None]

TOOLS_REPO = "CodeBoarding/tools"
TOOLS_TAG = "tools-2026.04.05"

JDTLS_VERSION = "1.44.0-202501301522"
JDTLS_URL_TEMPLATE = "https://download.eclipse.org/jdtls/milestones/{version}/jdt-language-server-{version}.tar.gz"

_PLATFORM_SUFFIX = {
    "Darwin": "macos",
    "Windows": "windows.exe",
    "Linux": "linux",
}

_PLATFORM_BIN_SUBDIR = {
    "windows": "win",
    "darwin": "macos",
    "linux": "linux",
}


# -- Registry definition ------------------------------------------------------


class ToolKind(StrEnum):
    """How a tool dependency is distributed and installed."""

    NATIVE = "native"  # Pre-built binary downloaded from GitHub releases
    NODE = "node"  # npm package installed via `npm install`
    ARCHIVE = "archive"  # Tarball downloaded and extracted from GitHub releases


class ConfigSection(StrEnum):
    """Top-level sections in the tool configuration dict."""

    TOOLS = "tools"
    LSP_SERVERS = "lsp_servers"


@dataclass(frozen=True)
class ToolSource:
    """Base class describing where to download a tool from."""

    tag: str


@dataclass(frozen=True)
class GitHubToolSource(ToolSource):
    """Tool binary hosted on a GitHub release (built by our pipeline).

    Attributes:
        repo: GitHub ``owner/repo`` (e.g. ``CodeBoarding/tools``).
        asset_template: Asset filename with ``{platform_suffix}`` placeholder.
        sha256: Per-platform SHA256 hashes keyed by platform suffix.
    """

    repo: str = ""
    asset_template: str = ""
    sha256: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class UpstreamToolSource(ToolSource):
    """Tool downloaded directly from an upstream provider (e.g. Eclipse).

    Attributes:
        url_template: Full download URL with a ``{version}`` placeholder.
    """

    url_template: str = ""


@dataclass(frozen=True)
class ToolDependency:
    """Declarative description of an external tool dependency.

    Attributes:
        key: Config key in VSCODE_CONFIG (e.g. "tokei", "python", "go").
        binary_name: Executable name on disk (e.g. "tokei", "pyright-langserver").
        kind: How the tool is distributed — native binary, npm package, or archive.
        config_section: Top-level key in get_config() — "tools" or "lsp_servers".
        source: Download source for native/archive tools (None for npm-only tools).
        npm_packages: npm packages to install for node tools.
        archive_subdir: Subdirectory name under bin/ for archive extraction.
        js_entry_file: JS entry point filename for Windows direct execution (e.g. "cli.mjs").
        js_entry_parent: Parent directory substring to locate the entry point (e.g. "typescript-language-server").
    """

    key: str
    binary_name: str
    kind: ToolKind
    config_section: ConfigSection
    source: ToolSource | None = None
    npm_packages: list[str] = field(default_factory=list)
    archive_subdir: str = ""
    js_entry_file: str = ""
    js_entry_parent: str = ""


TOOL_REGISTRY: list[ToolDependency] = [
    ToolDependency(
        key="tokei",
        binary_name="tokei",
        kind=ToolKind.NATIVE,
        config_section=ConfigSection.TOOLS,
        source=GitHubToolSource(
            tag=TOOLS_TAG,
            repo=TOOLS_REPO,
            asset_template="tokei-{platform_suffix}",
            sha256={},
        ),
    ),
    ToolDependency(
        key="go",
        binary_name="gopls",
        kind=ToolKind.NATIVE,
        config_section=ConfigSection.LSP_SERVERS,
        source=GitHubToolSource(
            tag=TOOLS_TAG,
            repo=TOOLS_REPO,
            asset_template="gopls-{platform_suffix}",
            sha256={},
        ),
    ),
    ToolDependency(
        key="python",
        binary_name="pyright-langserver",
        kind=ToolKind.NODE,
        config_section=ConfigSection.LSP_SERVERS,
        npm_packages=["pyright@1.1.400"],
        js_entry_file="langserver.index.js",
        js_entry_parent="pyright",
    ),
    ToolDependency(
        key="typescript",  # javascript uses the same LSP as typescript
        binary_name="typescript-language-server",
        kind=ToolKind.NODE,
        config_section=ConfigSection.LSP_SERVERS,
        npm_packages=["typescript-language-server@4.3.4", "typescript@5.7"],
        js_entry_file="cli.mjs",
        js_entry_parent="typescript-language-server",
    ),
    ToolDependency(
        key="php",
        binary_name="intelephense",
        kind=ToolKind.NODE,
        config_section=ConfigSection.LSP_SERVERS,
        npm_packages=["intelephense@1.16.5"],
        js_entry_file="intelephense.js",
        js_entry_parent="intelephense",
    ),
    ToolDependency(
        key="java",
        binary_name="java",
        kind=ToolKind.ARCHIVE,
        config_section=ConfigSection.LSP_SERVERS,
        source=UpstreamToolSource(
            tag=JDTLS_VERSION,
            url_template=JDTLS_URL_TEMPLATE,
        ),
        archive_subdir="jdtls",
    ),
]


# -- User data directory & manifest -------------------------------------------


def user_data_dir() -> Path:
    """Return the user-level persistent storage directory (~/.codeboarding)."""
    return Path.home() / ".codeboarding"


def get_servers_dir() -> Path:
    """Return the directory where language server binaries are installed."""
    return user_data_dir() / "servers"


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


def preferred_node_path(base_dir: Path) -> str | None:
    """Return the preferred Node.js binary for running JS-based language servers."""
    return os.environ.get("CODEBOARDING_NODE_PATH") or embedded_node_path(base_dir) or shutil.which("node")


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
    """Return the preferred command prefix for invoking npm."""
    if npm_path := embedded_npm_path(base_dir):
        return [npm_path]
    if npm_path := sibling_npm_path(os.environ.get("CODEBOARDING_NODE_PATH")):
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
    specs: list[str] = []
    for dep in TOOL_REGISTRY:
        if dep.kind is ToolKind.NODE:
            specs.extend(sorted(dep.npm_packages))
    return ",".join(specs)


def _tools_fingerprint() -> str:
    """Deterministic fingerprint of all pinned tool sources.

    Changes whenever a tool version or source in TOOL_REGISTRY is updated,
    causing ``needs_install()`` to trigger a reinstall.
    """
    parts: list[str] = []
    for dep in TOOL_REGISTRY:
        if dep.source:
            repo = dep.source.repo if isinstance(dep.source, GitHubToolSource) else ""
            parts.append(f"{dep.key}:{repo}:{dep.source.tag}")
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


def _acquire_lock(lock_fd: Any) -> None:
    """Acquire an exclusive file lock, logging if we have to wait."""
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


# -- Public API ----------------------------------------------------------------


def install_tools(target_dir: Path) -> None:
    """Download and install all registered tools to target_dir.

    Layout:
        <target_dir>/bin/<platform>/   — native binaries
        <target_dir>/bin/<subdir>/     — archive extractions (e.g. jdtls)
        <target_dir>/node_modules/     — Node-based tools
    """
    target_dir.mkdir(parents=True, exist_ok=True)

    native_deps = [d for d in TOOL_REGISTRY if d.kind is ToolKind.NATIVE]
    node_deps = [d for d in TOOL_REGISTRY if d.kind is ToolKind.NODE]
    archive_deps = [d for d in TOOL_REGISTRY if d.kind is ToolKind.ARCHIVE]

    if native_deps:
        install_native_tools(target_dir, native_deps)
    if node_deps:
        install_node_tools(target_dir, node_deps)
    for dep in archive_deps:
        install_archive_tool(target_dir, dep)


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


# -- Install helpers (used by install.py for granular control) -----------------


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


def _asset_url(source: ToolSource, asset_name: str) -> str:
    """Construct the download URL for a tool asset.

    For GitHub-hosted tools, builds a releases URL from repo/tag/asset.
    For upstream sources, the url_template is the full URL with a ``{version}`` placeholder.
    """
    if isinstance(source, UpstreamToolSource):
        return source.url_template.format(version=source.tag)
    if isinstance(source, GitHubToolSource):
        return f"https://github.com/{source.repo}/releases/download/{source.tag}/{asset_name}"
    raise TypeError(f"Unknown source type: {type(source)}")


def download_asset(url: str, destination: Path, expected_sha256: str | None = None) -> bool:
    """Download a file from *url* to *destination*. Returns True on success.

    Writes to a temp file first, then atomically renames to prevent
    corrupt binaries if the download is interrupted.  When *expected_sha256*
    is provided the downloaded content is verified against it.
    """
    destination.parent.mkdir(parents=True, exist_ok=True)
    temp_dest = destination.with_suffix(destination.suffix + ".download")
    try:
        response = requests.get(url, stream=True, timeout=300, allow_redirects=True)
        response.raise_for_status()
        with open(temp_dest, "wb") as f:
            for chunk in response.iter_content(chunk_size=32768):
                if chunk:
                    f.write(chunk)
        if temp_dest.stat().st_size == 0:
            temp_dest.unlink(missing_ok=True)
            return False
        if expected_sha256:
            actual = hashlib.sha256(temp_dest.read_bytes()).hexdigest()
            if actual != expected_sha256:
                temp_dest.unlink(missing_ok=True)
                raise ValueError(f"SHA256 mismatch for {destination.name}: expected {expected_sha256}, got {actual}")
        os.replace(temp_dest, destination)
        return True
    except Exception:
        temp_dest.unlink(missing_ok=True)
        raise


def install_native_tools(
    target_dir: Path,
    deps: list[ToolDependency],
    on_progress: ProgressCallback | None = None,
) -> None:
    """Download native binaries from their configured sources."""
    system = platform.system()
    suffix = _PLATFORM_SUFFIX.get(system)
    if suffix is None:
        logger.error("Unsupported platform: %s", system)
        return

    bin_dir = platform_bin_dir(target_dir)
    bin_dir.mkdir(parents=True, exist_ok=True)

    downloadable = [d for d in deps if isinstance(d.source, GitHubToolSource)]
    for i, dep in enumerate(downloadable, 1):
        if on_progress:
            on_progress(dep.binary_name, i, len(downloadable))
        binary_path = bin_dir / f"{dep.binary_name}{exe_suffix()}"
        if binary_path.exists():
            logger.info("  %s: already installed, skipping", dep.binary_name)
            continue
        source = dep.source
        assert isinstance(source, GitHubToolSource)
        asset_name = source.asset_template.format(platform_suffix=suffix)
        url = _asset_url(source, asset_name)
        expected_hash = source.sha256.get(suffix)
        try:
            if download_asset(url, binary_path, expected_sha256=expected_hash):
                if system != "Windows":
                    os.chmod(binary_path, 0o755)
                logger.info("  %s: downloaded successfully", dep.binary_name)
            else:
                logger.warning("  %s: download failed (empty file)", dep.binary_name)
                binary_path.unlink(missing_ok=True)
        except Exception:
            logger.exception("  %s: download failed", dep.binary_name)
            binary_path.unlink(missing_ok=True)


def install_node_tools(
    target_dir: Path,
    deps: list[ToolDependency],
    on_progress: ProgressCallback | None = None,
) -> None:
    """Install Node.js tools via npm."""
    npm_command = preferred_npm_command(target_dir)
    if not npm_command:
        logger.warning("npm not found. Skipping Node.js tool installation.")
        return

    # Collect all npm packages from all node deps
    all_packages: list[str] = []
    for dep in deps:
        all_packages.extend(dep.npm_packages)

    if not all_packages:
        return

    env = npm_subprocess_env(target_dir)
    if on_progress:
        on_progress("npm packages", 1, 1)
    logger.info("Installing Node.js packages: %s", all_packages)
    try:
        if not (target_dir / "package.json").exists():
            subprocess.run(
                [*npm_command, "init", "-y"], cwd=target_dir, check=True, capture_output=True, text=True, env=env
            )
        subprocess.run(
            [*npm_command, "install", *all_packages],
            cwd=target_dir,
            check=True,
            capture_output=True,
            text=True,
            env=env,
        )
        logger.info("Node.js packages installed successfully")
    except subprocess.CalledProcessError:
        logger.exception("Node.js package installation failed")
    except Exception:
        logger.exception("Node.js package installation failed")


def install_archive_tool(
    target_dir: Path,
    dep: ToolDependency,
    on_progress: ProgressCallback | None = None,
) -> None:
    """Download and extract an archive tool."""
    assert dep.source, f"{dep.key}: source required for archive tools"
    assert dep.archive_subdir, f"{dep.key}: archive_subdir required for archive tools"

    if on_progress:
        on_progress(dep.key, 1, 1)

    extract_dir = target_dir / "bin" / dep.archive_subdir
    if extract_dir.exists() and (extract_dir / "plugins").is_dir():
        logger.info("%s already installed", dep.key)
        return

    logger.info("Downloading %s...", dep.key)
    extract_dir.mkdir(parents=True, exist_ok=True)
    archive_path = target_dir / "bin" / f"{dep.archive_subdir}.tar.gz"

    url = _asset_url(dep.source, "")
    expected_hash = dep.source.sha256.get("") if isinstance(dep.source, GitHubToolSource) else None
    try:
        if not download_asset(url, archive_path, expected_sha256=expected_hash):
            logger.warning("%s download failed (empty file)", dep.key)
            return

        with tarfile.open(archive_path, "r:gz") as tar:
            tar.extractall(path=extract_dir, filter="tar")
        archive_path.unlink()
        logger.info("%s installed successfully", dep.key)
    except Exception:
        logger.exception("%s installation failed", dep.key)
        archive_path.unlink(missing_ok=True)
