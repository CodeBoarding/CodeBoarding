"""Declarative registry of external tool dependencies.

This is the single source of truth for what tools CodeBoarding needs,
how to install them, and how to locate them. Both Core's install.py
and the wrapper's tool_config.py delegate to this module.

Adding a new language/tool:
    1. Add a ToolDependency entry to TOOL_REGISTRY below
    2. Add the corresponding config entry to VSCODE_CONFIG in vscode_constants.py
    3. Add to the Language enum in static_analyzer/constants.py
    That's it — install, resolve, and wrapper pick it up automatically.

Package layout:
    tool_registry/
      __init__.py       (this file -- data model, registry data, re-exports)
      paths.py          (filesystem paths and Node.js runtime resolution)
      manifest.py       (fingerprint, needs_install, lock, config resolution)
      installers.py     (download_asset, install_*, nodeenv bootstrap)

Every name that external callers currently import from ``tool_registry`` is
re-exported at the bottom of this file, so the public API is unchanged.
"""

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum


# -- Logger --------------------------------------------------------------------


logger = logging.getLogger(__name__)


# -- Public types and constants -----------------------------------------------


# Callback type for reporting download progress: (tool_name, current_step, total_steps)
ProgressCallback = Callable[[str, int, int], None]

TOOLS_REPO = "CodeBoarding/tools"
TOOLS_TAG = "tools-2026.04.05"

JDTLS_VERSION = "1.44.0"
JDTLS_BUILD = "202501221502"
JDTLS_URL_TEMPLATE = (
    "https://download.eclipse.org/jdtls/milestones/{version}/jdt-language-server-{version}-{build}.tar.gz"
)

# Pinned Node.js runtime used when the user has no system Node. Downloaded on
# first run into ``<servers_dir>/nodeenv/`` via the ``nodeenv`` Python package
# (see install_embedded_node()).  Bump deliberately — a version change is
# folded into _tools_fingerprint() and triggers a full reinstall.
PINNED_NODE_VERSION = "20.18.1"

_PLATFORM_SUFFIX = {
    "Darwin": "macos",
    "Windows": "windows.exe",
    "Linux": "linux",
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
        url_template: Full download URL with ``{version}`` and optional ``{build}`` placeholders.
        build: Build identifier appended to the version in the download URL.
    """

    url_template: str = ""
    build: str = ""


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
            sha256={
                "linux": "e366026993bce6a40d6df19dcac9c1c58e88820268c68304c056cd3878e545e2",
                "macos": "90ae8a2e979b9658c2616787bcc26f187f14b922fcd0bf61cb3f7fcc2a43634e",
                "windows.exe": "7db547cb6bfa1722e89ca52a43426fb212aa53603a60256af25fb6e59ca12099",
            },
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
            sha256={
                "linux": "76ecc01106266aa03f75c3ea857f6bd6a1da79b00abb6cb5a573b1cd5ecbdcb7",
                "macos": "a12551ec82e8000c055a8e8e3447cbf22bd7c4b220d4e3802112a569e88a4965",
                "windows.exe": "b739c89bcd3068257a5ac1be1b9b4978576f7731c7893fdc0b13577927bd6483",
            },
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
            build=JDTLS_BUILD,
        ),
        archive_subdir="jdtls",
    ),
]


# -- Submodule re-exports -----------------------------------------------------
#
# Everything below is a re-export of names that live in submodules.  They
# are imported here AFTER the registry data above so that submodules whose
# functions do ``from . import TOOL_REGISTRY`` see a fully-initialized
# package namespace at call time, avoiding circular-import failures.
#
# External callers (``install.py``, ``main.py``, ``utils.py``, wrapper code,
# and tests) can continue importing these names directly from ``tool_registry``
# as before.

# ruff / flake8: the re-exports below are intentional — ``noqa: F401, E402``.

from .paths import (  # noqa: F401, E402
    MINIMUM_NODE_MAJOR_VERSION,
    _node_is_acceptable,
    _node_version_tuple,
    embedded_node_path,
    embedded_npm_cli_path,
    embedded_npm_path,
    exe_suffix,
    get_servers_dir,
    nodeenv_bin_dir,
    nodeenv_root_dir,
    npm_subprocess_env,
    platform_bin_dir,
    preferred_node_path,
    preferred_npm_command,
    sibling_npm_path,
    user_data_dir,
)
from .manifest import (  # noqa: F401, E402
    _installed_version,
    _manifest_path,
    _npm_specs_fingerprint,
    _read_manifest,
    _tools_fingerprint,
    acquire_lock,
    build_config,
    has_required_tools,
    needs_install,
    resolve_config,
    resolve_config_from_path,
    write_manifest,
)
from .installers import (  # noqa: F401, E402
    _NODEENV_VERSION_STAMP,
    _asset_url,
    _embedded_node_is_healthy,
    _initialize_nodeenv_globals,
    _nodeenv_needs_unofficial_builds,
    download_asset,
    install_archive_tool,
    install_embedded_node,
    install_native_tools,
    install_node_tools,
    install_tools,
)
