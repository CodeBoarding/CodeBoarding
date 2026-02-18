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

import logging
import os
import platform
import shutil
import subprocess
import tarfile
from copy import deepcopy
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

import requests

logger = logging.getLogger(__name__)

GITHUB_REPO = "CodeBoarding/CodeBoarding"

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
class ToolDependency:
    """Declarative description of an external tool dependency.

    Attributes:
        key: Config key in VSCODE_CONFIG (e.g. "tokei", "python", "go").
        binary_name: Executable name on disk (e.g. "tokei", "pyright-langserver").
        kind: How the tool is distributed — native binary, npm package, or archive.
        config_section: Top-level key in get_config() — "tools" or "lsp_servers".
        github_asset_template: Asset name with {platform_suffix} placeholder for native binaries.
        npm_packages: npm packages to install for node tools.
        archive_asset: Asset name for archive tools (e.g. "jdtls.tar.gz").
        archive_subdir: Subdirectory name under bin/ for archive extraction.
    """

    key: str
    binary_name: str
    kind: ToolKind
    config_section: ConfigSection
    github_asset_template: str = ""
    npm_packages: list[str] = field(default_factory=list)
    archive_asset: str = ""
    archive_subdir: str = ""


TOOL_REGISTRY: list[ToolDependency] = [
    ToolDependency(
        key="tokei",
        binary_name="tokei",
        kind=ToolKind.NATIVE,
        config_section=ConfigSection.TOOLS,
        github_asset_template="tokei-{platform_suffix}",
    ),
    ToolDependency(
        key="go",
        binary_name="gopls",
        kind=ToolKind.NATIVE,
        config_section=ConfigSection.LSP_SERVERS,
        github_asset_template="gopls-{platform_suffix}",
    ),
    ToolDependency(
        key="python",
        binary_name="pyright-langserver",
        kind=ToolKind.NODE,
        config_section=ConfigSection.LSP_SERVERS,
        npm_packages=["pyright"],
    ),
    ToolDependency(
        key="typescript",
        binary_name="typescript-language-server",
        kind=ToolKind.NODE,
        config_section=ConfigSection.LSP_SERVERS,
        npm_packages=["typescript-language-server", "typescript"],
    ),
    ToolDependency(
        key="php",
        binary_name="intelephense",
        kind=ToolKind.NODE,
        config_section=ConfigSection.LSP_SERVERS,
        npm_packages=["intelephense"],
    ),
    ToolDependency(
        key="java",
        binary_name="java",
        kind=ToolKind.ARCHIVE,
        config_section=ConfigSection.LSP_SERVERS,
        archive_asset="jdtls.tar.gz",
        archive_subdir="jdtls",
    ),
]


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
    from vscode_constants import VSCODE_CONFIG

    config = deepcopy(VSCODE_CONFIG)
    bin_dir = platform_bin_dir(base_dir)
    is_win = platform.system() == "Windows"
    native_ext = ".exe" if is_win else ""
    node_ext = ".cmd" if is_win else ""

    for dep in TOOL_REGISTRY:
        if dep.kind is ToolKind.NATIVE:
            binary_path = bin_dir / f"{dep.binary_name}{native_ext}"
            if binary_path.exists():
                config[dep.config_section][dep.key]["command"][0] = str(binary_path)

        elif dep.kind is ToolKind.NODE:
            binary_path = base_dir / "node_modules" / ".bin" / f"{dep.binary_name}{node_ext}"
            if binary_path.exists():
                config[dep.config_section][dep.key]["command"][0] = str(binary_path)

        elif dep.kind is ToolKind.ARCHIVE and dep.archive_subdir:
            archive_dir = base_dir / "bin" / dep.archive_subdir
            if archive_dir.is_dir() and (archive_dir / "plugins").is_dir():
                config[dep.config_section][dep.key]["jdtls_root"] = str(archive_dir)

    return config


def resolve_config_from_path() -> dict[str, Any]:
    """Discover tools on the system PATH and return a config dict."""
    from vscode_constants import VSCODE_CONFIG

    config = deepcopy(VSCODE_CONFIG)

    for dep in TOOL_REGISTRY:
        if dep.kind in (ToolKind.NATIVE, ToolKind.NODE):
            path = shutil.which(dep.binary_name)
            if path:
                config[dep.config_section][dep.key]["command"][0] = path

    return config


def has_required_tools(base_dir: Path) -> bool:
    """Check if the minimum required tools (tokei) are installed."""
    if not base_dir.exists():
        return False
    bin_dir = platform_bin_dir(base_dir)
    is_win = platform.system() == "Windows"
    tokei = bin_dir / ("tokei.exe" if is_win else "tokei")
    return tokei.exists()


# -- Install helpers (used by install.py for granular control) -----------------


def platform_bin_dir(base: Path) -> Path:
    """Return the platform-specific binary directory under base (e.g. base/bin/macos)."""
    system = platform.system().lower()
    subdir = _PLATFORM_BIN_SUBDIR.get(system)
    if subdir is None:
        raise RuntimeError(f"Unsupported platform: {system}")
    return base / "bin" / subdir


def get_latest_release_tag() -> str:
    """Fetch the latest release tag from the GitHub repository."""
    response = requests.get(f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest", timeout=30)
    response.raise_for_status()
    return response.json()["tag_name"]


def download_asset(tag: str, asset_name: str, destination: Path) -> bool:
    """Download a GitHub release asset to destination. Returns True on success."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    url = f"https://github.com/{GITHUB_REPO}/releases/download/{tag}/{asset_name}"
    response = requests.get(url, stream=True, timeout=300, allow_redirects=True)
    response.raise_for_status()
    with open(destination, "wb") as f:
        for chunk in response.iter_content(chunk_size=32768):
            if chunk:
                f.write(chunk)
    return destination.exists() and destination.stat().st_size > 0


def install_native_tools(target_dir: Path, deps: list[ToolDependency]) -> None:
    """Download native binaries from GitHub releases."""
    system = platform.system()
    suffix = _PLATFORM_SUFFIX.get(system)
    if suffix is None:
        logger.error("Unsupported platform: %s", system)
        return

    bin_dir = platform_bin_dir(target_dir)
    bin_dir.mkdir(parents=True, exist_ok=True)

    try:
        tag = get_latest_release_tag()
        logger.info("Using release: %s", tag)
    except Exception:
        logger.exception("Could not determine latest release")
        return

    is_win = system == "Windows"
    for dep in deps:
        assert dep.github_asset_template, f"{dep.key}: github_asset_template required for native tools"
        asset_name = dep.github_asset_template.format(platform_suffix=suffix)
        ext = ".exe" if is_win else ""
        binary_path = bin_dir / (dep.binary_name + ext)
        try:
            if download_asset(tag, asset_name, binary_path):
                if not is_win:
                    os.chmod(binary_path, 0o755)
                logger.info("  %s: downloaded successfully", dep.binary_name)
            else:
                logger.warning("  %s: download failed (empty file)", dep.binary_name)
                binary_path.unlink(missing_ok=True)
        except Exception:
            logger.exception("  %s: download failed", dep.binary_name)
            binary_path.unlink(missing_ok=True)


def install_node_tools(target_dir: Path, deps: list[ToolDependency]) -> None:
    """Install Node.js tools via npm."""
    npm_path = shutil.which("npm")
    if not npm_path:
        logger.warning("npm not found. Skipping Node.js tool installation.")
        return

    # Collect all npm packages from all node deps
    all_packages: list[str] = []
    for dep in deps:
        all_packages.extend(dep.npm_packages)

    if not all_packages:
        return

    logger.info("Installing Node.js packages: %s", all_packages)
    original_cwd = os.getcwd()
    try:
        os.chdir(target_dir)
        if not Path("package.json").exists():
            subprocess.run([npm_path, "init", "-y"], check=True, capture_output=True, text=True)
        subprocess.run(
            [npm_path, "install", *all_packages],
            check=True,
            capture_output=True,
            text=True,
        )
        logger.info("Node.js packages installed successfully")
    except subprocess.CalledProcessError:
        logger.exception("Node.js package installation failed")
    except Exception:
        logger.exception("Node.js package installation failed")
    finally:
        os.chdir(original_cwd)


def install_archive_tool(target_dir: Path, dep: ToolDependency) -> None:
    """Download and extract an archive tool."""
    assert dep.archive_asset, f"{dep.key}: archive_asset required for archive tools"
    assert dep.archive_subdir, f"{dep.key}: archive_subdir required for archive tools"

    extract_dir = target_dir / "bin" / dep.archive_subdir
    if extract_dir.exists() and (extract_dir / "plugins").is_dir():
        logger.info("%s already installed", dep.key)
        return

    logger.info("Downloading %s...", dep.key)
    extract_dir.mkdir(parents=True, exist_ok=True)
    archive_path = target_dir / "bin" / dep.archive_asset

    try:
        tag = get_latest_release_tag()
        if not download_asset(tag, dep.archive_asset, archive_path):
            logger.warning("%s download failed (empty file)", dep.key)
            return

        with tarfile.open(archive_path, "r:gz") as tar:
            tar.extractall(path=extract_dir, filter="tar")
        archive_path.unlink()
        logger.info("%s installed successfully", dep.key)
    except Exception:
        logger.exception("%s installation failed", dep.key)
        archive_path.unlink(missing_ok=True)
