"""Registry data and type definitions (leaf module, imports no siblings).

Adding a new tool:
    1. Add a ``ToolDependency`` entry to ``TOOL_REGISTRY`` below.
       For native binaries hosted as a single pre-extracted file, set
       ``ToolKind.NATIVE`` and a ``GitHubToolSource`` with ``asset_template``.
       For native binaries shipped as compressed assets (gzipped on Unix or
       zipped on Windows — e.g. upstream rust-analyzer), additionally set
       ``asset_arch_overrides`` (format is inferred from the asset filename suffix).
    2. Add the entry to ``VSCODE_CONFIG`` in ``vscode_constants.py``.
    3. Add to the ``Language`` enum in ``static_analyzer/config.py``.
"""

import json
import platform
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any


# -- Public types and constants -----------------------------------------------


# (tool_name, current_step, total_steps)
ProgressCallback = Callable[[str, int, int], None]

TOOLS_REPO = "CodeBoarding/tools"
TOOLS_TAG = "tools-2026.04.05"

JDTLS_VERSION = "1.59.0"
JDTLS_BUILD = "202605111959"
JDTLS_URL_TEMPLATE = "https://download.eclipse.org/jdtls/snapshots/jdt-language-server-{version}-{build}.tar.gz"

# rust-analyzer is pulled directly from upstream (weekly releases, ~17MB
# per platform) rather than mirrored. Bumping the tag triggers a reinstall
# via ``tool_fingerprint()``.
RUST_ANALYZER_REPO = "rust-lang/rust-analyzer"
RUST_ANALYZER_TAG = "2026-10-26"

# JetBrains' kotlin-lsp ships one archive per platform with its own Java runtime inside. Only early-access builds
# exist, and each stops starting about 30 days after its build date, so a pin compiled into a release would die in
# every installed copy. The pin is a file instead: the wheel's copy is the fallback, and installed Core reads main's
# copy when a run needs Kotlin, so merging a bump of that file is what publishes it. The file only points at a build:
# the archives always come from JetBrains, whose licence (section 3.4(d)) rules out mirroring them. The "Kotlin
# server" workflow opens those bumps from the ``<archive>.sha256`` files JetBrains publishes beside each archive.
KOTLIN_LSP_PIN_FILE = "kotlin-server-1.json"
# A public contract: every installed Core reads this path for as long as it runs, so the file cannot move. When
# JetBrains changes its layout or URLs, the new form goes in a new numbered file and older Core stays on the last
# build it can run.
KOTLIN_LSP_PIN_URL = (
    f"https://raw.githubusercontent.com/CodeBoarding/CodeBoarding/main/infra/tool_registry/{KOTLIN_LSP_PIN_FILE}"
)
KOTLIN_LSP_URL_TEMPLATE = "https://download.jetbrains.com/language-server/kotlin-server/{version}/{asset}"
KOTLIN_LSP_LICENSE_URL = "https://github.com/Kotlin/kotlin-lsp/blob/main/kotlin-vscode/LICENSE.txt"
KOTLIN_LSP_ASSETS = {
    ("Linux", "x86_64"): "kotlin-server-{version}.tar.gz",
    ("Linux", "aarch64"): "kotlin-server-{version}-aarch64.tar.gz",
    ("Darwin", "x86_64"): "kotlin-server-{version}.sit",
    ("Darwin", "arm64"): "kotlin-server-{version}-aarch64.sit",
    ("Windows", "AMD64"): "kotlin-server-{version}.win.zip",
    ("Windows", "ARM64"): "kotlin-server-{version}-aarch64.win.zip",
}
_KOTLIN_LSP_VERSION = re.compile(r"[0-9]{1,6}\.[0-9]{1,6}\.[0-9]{1,6}")
_SHA256 = re.compile(r"[0-9a-f]{64}")

# Pinned Node.js runtime for users without system Node; downloaded to
# <servers_dir>/nodeenv/ via install_embedded_node(). A bump is folded into
# the Node tools' tool_fingerprint() and reinstalls them.
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

    url_template: str = ""  # with ``{version}`` / optional ``{build}`` / ``{asset}``
    build: str = ""
    # Per-host archive, keyed by ``(platform.system(), platform.machine())``, and its sha256 by asset name.
    asset_arch_overrides: dict[tuple[str, str], str] = field(default_factory=dict)
    sha256: dict[str, str] = field(default_factory=dict)


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
    # Launcher inside an extracted archive, relative to ``bin/<archive_subdir>``, with ``.exe``
    # on Windows. Empty for an archive the adapter launches itself (JDTLS).
    archive_entry: str = ""
    # Terms shown when the tool is downloaded, for one not under an open-source licence.
    license_url: str = ""
    js_entry_file: str = ""
    js_entry_parent: str = ""

    def is_available_on_host(self) -> bool:
        """True unless this is an arch-aware dep whose override map excludes the running
        ``(system, machine)`` (e.g. rust-analyzer on Linux/riscv64). Consulted by both the
        installer and ``has_required_tools`` to keep them in sync.
        """
        if self.kind not in (ToolKind.NATIVE, ToolKind.ARCHIVE):
            return True
        if not isinstance(self.source, (GitHubToolSource, UpstreamToolSource)):
            return True
        if not self.source.asset_arch_overrides:
            return True
        return (platform.system(), platform.machine()) in self.source.asset_arch_overrides


def kotlin_lsp_build(version: Any) -> tuple[int, ...]:
    """*version* as numbers to compare builds by. Raises ``ValueError`` for anything else: it goes into a URL."""
    if not isinstance(version, str) or not _KOTLIN_LSP_VERSION.fullmatch(version):
        raise ValueError(f"not a kotlin-lsp version: {version!r}")
    return tuple(int(part) for part in version.split("."))


def kotlin_lsp_source(pin: Any) -> UpstreamToolSource:
    """The source a pin file describes. Raises ``ValueError`` for anything less: a pin can come off the network."""
    if not isinstance(pin, dict):
        raise ValueError(f"a kotlin-lsp pin is an object, not {type(pin).__name__}")
    version = pin.get("version", "")
    kotlin_lsp_build(version)
    assets = {host: name.format(version=version) for host, name in KOTLIN_LSP_ASSETS.items()}
    sha256 = pin.get("sha256")
    if (
        not isinstance(sha256, dict)
        or set(sha256) != set(assets.values())
        or not all(isinstance(value, str) and _SHA256.fullmatch(value) for value in sha256.values())
    ):
        raise ValueError(f"kotlin-lsp {version} does not carry a checksum for each of its archives")
    return UpstreamToolSource(
        tag=version, url_template=KOTLIN_LSP_URL_TEMPLATE, asset_arch_overrides=assets, sha256=sha256
    )


# Installed before any language is known: tokei is what finds a repository's languages.
BASE_TOOL_KEYS = frozenset({"tokei"})

TOOL_REGISTRY: list[ToolDependency] = [
    ToolDependency(
        key="cpp",
        binary_name="clangd",
        kind=ToolKind.ARCHIVE,
        config_section=ConfigSection.LSP_SERVERS,
        source=GitHubToolSource(
            tag="23.1.0",
            repo="clangd/clangd",
            asset_arch_overrides={
                ("Linux", "x86_64"): "clangd-linux-23.1.0.zip",
                ("Darwin", "x86_64"): "clangd-mac-23.1.0.zip",
                ("Darwin", "arm64"): "clangd-mac-23.1.0.zip",
                ("Windows", "AMD64"): "clangd-windows-23.1.0.zip",
            },
            sha256={
                "clangd-linux-23.1.0.zip": "e53b1a96196095faedb7642cf64964f7fb9ad4a0c1f00dd2c172a3d9dcbafdfd",
                "clangd-mac-23.1.0.zip": "1082e6638223b785ca2daf0939f13afcd0bb95c84ee9a4bbaff4745365159253",
                "clangd-windows-23.1.0.zip": "23412a240756a162e7b98a282f36aa2a23a88db5ce16a0cbc4fef7253768c810",
            },
        ),
        archive_subdir="clangd",
        archive_entry="bin/clangd",
    ),
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
    # Pin to 0.24.0 so C# document symbols work reliably for modern .NET 10
    # repositories. ``--tool-path`` avoids a misleading "DotnetToolSettings.xml
    # not found" error when no local manifest is present; the package's default
    # target framework is selected by dotnet.
    ToolDependency(
        key="csharp",
        binary_name="csharp-ls",
        kind=ToolKind.PACKAGE_MANAGER,
        config_section=ConfigSection.LSP_SERVERS,
        source=PackageManagerToolSource(
            tag="0.24.0",
            manager_binary="dotnet",
            install_args=(
                "tool",
                "install",
                "csharp-ls",
                "--version",
                "{tag}",
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
        key="kotlin",
        binary_name="intellij-server",
        kind=ToolKind.ARCHIVE,
        config_section=ConfigSection.LSP_SERVERS,
        source=kotlin_lsp_source(json.loads((Path(__file__).parent / KOTLIN_LSP_PIN_FILE).read_text("utf-8"))),
        archive_subdir="kotlin-lsp",
        archive_entry="bin/intellij-server",
        license_url=KOTLIN_LSP_LICENSE_URL,
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
            # non-empty so ``tool_fingerprint()`` formatting stays stable.
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

ALL_LANGUAGE_KEYS = frozenset(dep.key for dep in TOOL_REGISTRY if dep.config_section is ConfigSection.LSP_SERVERS)
