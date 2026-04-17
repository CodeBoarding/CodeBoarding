"""Registry data and type definitions (leaf module, imports no siblings).

Adding a new tool:
    1. Add a ``ToolDependency`` entry to ``TOOL_REGISTRY`` below.
       For native binaries hosted as a single pre-extracted file, set
       ``ToolKind.NATIVE`` and a ``GitHubToolSource`` with ``asset_template``.
       For native binaries shipped as compressed assets (gzipped on Unix or
       zipped on Windows — e.g. upstream rust-analyzer), additionally set
       ``asset_arch_overrides`` (format is inferred from the asset filename suffix).
    2. Add the entry to ``VSCODE_CONFIG`` in ``vscode_constants.py``.
    3. Add to the ``Language`` enum in ``static_analyzer/constants.py``.
"""

import platform
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum


# -- Public types and constants -----------------------------------------------


# (tool_name, current_step, total_steps)
ProgressCallback = Callable[[str, int, int], None]

TOOLS_REPO = "CodeBoarding/tools"
TOOLS_TAG = "tools-2026.04.05"

JDTLS_VERSION = "1.44.0"
JDTLS_BUILD = "202501221502"
JDTLS_URL_TEMPLATE = (
    "https://download.eclipse.org/jdtls/milestones/{version}/jdt-language-server-{version}-{build}.tar.gz"
)

# rust-analyzer is pulled directly from upstream (weekly releases, ~17MB
# per platform) rather than mirrored. Bumping the tag triggers a reinstall
# via ``tools_fingerprint()``.
RUST_ANALYZER_REPO = "rust-lang/rust-analyzer"
RUST_ANALYZER_TAG = "2026-03-30"

# Pinned Node.js runtime for users without system Node; downloaded to
# <servers_dir>/nodeenv/ via install_embedded_node(). A bump is folded into
# tools_fingerprint() and triggers a full reinstall.
PINNED_NODE_VERSION = "20.18.1"

PLATFORM_SUFFIX = {
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
    PACKAGE_MANAGER = (
        "package_manager"  # Installed by invoking a user-provided package manager (e.g. `dotnet tool install`)
    )


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
    """Tool binary hosted on a GitHub release.

    Distribution patterns:

    * **Pre-extracted binary** (default, e.g. ``tokei``, ``gopls``): asset
      downloaded directly to ``bin/<platform>/<binary_name><exe>``.
    * **Compressed binary** (e.g. ``rust-analyzer``): the installer infers
      the format from the asset filename suffix (``.gz`` or ``.zip``) and
      decompresses after download. ``archive_inner_path`` picks a specific
      member out of a zip; single-member zips can omit it.
    * **Architecture-aware**: provide ``asset_arch_overrides`` keyed by
      ``(platform.system(), platform.machine())`` for tools shipping
      distinct binaries per CPU; the override wins over the
      ``asset_template`` + ``PLATFORM_SUFFIX`` lookup.
    """

    repo: str = ""
    asset_template: str = ""  # ``{platform_suffix}`` placeholder
    sha256: dict[str, str] = field(default_factory=dict)  # keyed by platform suffix
    archive_inner_path: str = ""  # path to the binary inside a zip; default = single member
    asset_arch_overrides: dict[tuple[str, str], str] = field(default_factory=dict)


@dataclass(frozen=True)
class UpstreamToolSource(ToolSource):
    """Tool downloaded directly from an upstream provider (e.g. Eclipse)."""

    url_template: str = ""  # with ``{version}`` / optional ``{build}``
    build: str = ""


@dataclass(frozen=True)
class PackageManagerToolSource(ToolSource):
    """Tool installed by invoking a user-provided package manager (e.g. ``dotnet tool install``).

    Why: some LSPs ship only as language-package-manager packages, not as standalone binaries.
    """

    manager_binary: str = ""  # e.g. "dotnet"
    # Placeholders substituted at install time: ``{tool_path}`` -> managed install dir; ``{tag}`` -> source.tag.
    install_args: tuple[str, ...] = ()


@dataclass(frozen=True)
class ToolDependency:
    """Declarative description of an external tool dependency."""

    key: str
    binary_name: str
    kind: ToolKind
    config_section: ConfigSection
    source: ToolSource | None = None
    npm_packages: list[str] = field(default_factory=list)
    archive_subdir: str = ""
    js_entry_file: str = ""
    js_entry_parent: str = ""

    def is_available_on_host(self) -> bool:
        """True unless this is an arch-aware NATIVE dep whose override map
        excludes the running ``(system, machine)`` (e.g. rust-analyzer on
        Linux/riscv64). Consulted by both the installer and
        ``has_required_tools`` to keep them in sync.
        """
        if self.kind is not ToolKind.NATIVE:
            return True
        if not isinstance(self.source, GitHubToolSource):
            return True
        if not self.source.asset_arch_overrides:
            return True
        return (platform.system(), platform.machine()) in self.source.asset_arch_overrides


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
    # csharp-ls ships only as a NuGet dotnet-tool; installed via ``dotnet tool install``.
    # Pinned 0.20.0: 0.21.0+ has a malformed NuGet upstream.
    # During .NET 10 migration we request ``--framework net10.0``; ``--tool-path``
    # avoids a misleading "DotnetToolSettings.xml not found" error when no local
    # manifest is present.
    ToolDependency(
        key="csharp",
        binary_name="csharp-ls",
        kind=ToolKind.PACKAGE_MANAGER,
        config_section=ConfigSection.LSP_SERVERS,
        source=PackageManagerToolSource(
            tag="0.20.0",
            manager_binary="dotnet",
            install_args=(
                "tool",
                "install",
                "csharp-ls",
                "--version",
                "{tag}",
                "--framework",
                "net10.0",
                "--tool-path",
                "{tool_path}",
            ),
        ),
        archive_subdir="csharp-ls",
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
    ToolDependency(
        key="rust",
        binary_name="rust-analyzer",
        kind=ToolKind.NATIVE,
        config_section=ConfigSection.LSP_SERVERS,
        source=GitHubToolSource(
            tag=RUST_ANALYZER_TAG,
            repo=RUST_ANALYZER_REPO,
            # ``asset_template`` is unused for arch-aware tools but kept
            # non-empty so ``tools_fingerprint()`` formatting stays stable.
            asset_template="rust-analyzer-{platform_suffix}",
            asset_arch_overrides={
                ("Linux", "x86_64"): "rust-analyzer-x86_64-unknown-linux-gnu.gz",
                ("Linux", "aarch64"): "rust-analyzer-aarch64-unknown-linux-gnu.gz",
                ("Darwin", "x86_64"): "rust-analyzer-x86_64-apple-darwin.gz",
                ("Darwin", "arm64"): "rust-analyzer-aarch64-apple-darwin.gz",
                ("Windows", "AMD64"): "rust-analyzer-x86_64-pc-windows-msvc.zip",
                ("Windows", "ARM64"): "rust-analyzer-aarch64-pc-windows-msvc.zip",
            },
        ),
    ),
]
