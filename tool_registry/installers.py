"""Tool installers: download, npm install, archive extract, embedded Node bootstrap.

The only module in the package with filesystem side effects.
"""

import gzip
import hashlib
import logging
import os
import platform
import shutil
import subprocess
import tarfile
import zipfile
from pathlib import Path
from typing import Any, cast

import requests

from .paths import (
    embedded_node_path,
    exe_suffix,
    node_version_tuple,
    nodeenv_root_dir,
    npm_subprocess_env,
    platform_bin_dir,
    preferred_npm_command,
)
from .registry import (
    PINNED_NODE_VERSION,
    PLATFORM_SUFFIX,
    TOOL_REGISTRY,
    GitHubToolSource,
    PackageManagerToolSource,
    ProgressCallback,
    ToolDependency,
    ToolKind,
    ToolSource,
    UpstreamToolSource,
)

logger = logging.getLogger(__name__)


# -- Download primitive -------------------------------------------------------


def asset_url(source: ToolSource, asset_name: str) -> str:
    """Construct the download URL for a tool asset."""
    if isinstance(source, UpstreamToolSource):
        return source.url_template.format(version=source.tag, build=source.build)
    if isinstance(source, GitHubToolSource):
        return f"https://github.com/{source.repo}/releases/download/{source.tag}/{asset_name}"
    raise TypeError(f"Unknown source type: {type(source)}")


def resolve_native_asset_name(source: GitHubToolSource, platform_suffix: str) -> str | None:
    """Pick the correct release asset filename for this host's (OS, arch).

    Architecture-aware sources (e.g. rust-analyzer) carry an
    ``asset_arch_overrides`` dict keyed by ``(platform.system(), platform.machine())``;
    when the current host has an entry there it wins over the templated name.

    Returns ``None`` when the host is unsupported (no override and no
    ``platform_suffix``) so the caller can log+skip rather than crash.
    """
    if source.asset_arch_overrides:
        key = (platform.system(), platform.machine())
        override = source.asset_arch_overrides.get(key)
        if override:
            return override
        # Architecture-aware tool but this host's (system, machine) isn't
        # listed — return None and let install_native_tools log a clear
        # "unsupported architecture" warning instead of falling through to
        # the templated name and downloading the wrong binary.
        return None
    if not platform_suffix:
        return None
    return source.asset_template.format(platform_suffix=platform_suffix)


def _is_compressed_asset(asset_name: str) -> bool:
    """True if the asset filename indicates it needs decompression."""
    lower = asset_name.lower()
    return lower.endswith(".gz") or lower.endswith(".zip")


def _extract_compressed_binary(archive_path: Path, inner_path: str, target: Path) -> None:
    """Atomically decompress *archive_path* (format inferred from suffix) into *target*.

    ``.gz`` is a single gzipped binary. ``.zip`` extracts ``inner_path`` if
    set, otherwise the only ``.exe`` member or — failing that — the only
    member. Raises ``ValueError`` for unknown suffixes or ambiguous zips.

    The decompressed bytes are written to a sibling ``<target>.extract``
    temp file and then ``os.replace``-d into place so a crash mid-extract
    cannot leave a half-written binary at *target*. The temp file is
    cleaned up on any exception.
    """
    suffix = archive_path.suffix.lower()
    if suffix not in (".gz", ".zip"):
        raise ValueError(f"Unsupported archive suffix: {suffix!r}")

    temp_target = target.with_name(target.name + ".extract")
    try:
        if suffix == ".gz":
            with gzip.open(archive_path, "rb") as src, open(temp_target, "wb") as dst:
                shutil.copyfileobj(src, dst)
        else:  # ".zip"
            with zipfile.ZipFile(archive_path) as zf:
                members = zf.namelist()
                if inner_path:
                    if inner_path not in members:
                        raise ValueError(
                            f"{archive_path.name}: archive_inner_path {inner_path!r} not found " f"(members: {members})"
                        )
                    chosen = inner_path
                else:
                    exe_members = [m for m in members if m.lower().endswith(".exe")]
                    if len(exe_members) == 1:
                        chosen = exe_members[0]
                    elif len(members) == 1:
                        chosen = members[0]
                    else:
                        raise ValueError(
                            f"{archive_path.name}: cannot pick a binary automatically "
                            f"(set archive_inner_path; members: {members})"
                        )
                with zf.open(chosen) as src, open(temp_target, "wb") as dst:
                    shutil.copyfileobj(src, dst)
        os.replace(temp_target, target)
    except Exception:
        temp_target.unlink(missing_ok=True)
        raise


def download_asset(url: str, destination: Path, expected_sha256: str | None = None) -> bool:
    """Download *url* to *destination* via temp-file + rename. Verifies SHA256 if provided."""
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


# -- Native binary installer --------------------------------------------------


def install_native_tools(
    target_dir: Path,
    deps: list[ToolDependency],
    on_progress: ProgressCallback | None = None,
) -> None:
    """Download native binaries from their configured sources.

    Supports both pre-extracted binaries (default, e.g. ``tokei``,
    ``gopls``) and compressed-binary assets (``rust-analyzer``); the
    archive format is inferred from the asset filename suffix.
    """
    system = platform.system()
    suffix = PLATFORM_SUFFIX.get(system)
    try:
        bin_dir = platform_bin_dir(target_dir)
    except RuntimeError:
        # ``platform_bin_dir`` raises on hosts not in ``_PLATFORM_BIN_SUBDIR``
        # (e.g. FreeBSD). Log once and return — without a target directory
        # we cannot install anything regardless of arch-override status.
        logger.warning(
            "Unsupported platform %s/%s; skipping native tool installation.",
            system,
            platform.machine(),
        )
        return
    bin_dir.mkdir(parents=True, exist_ok=True)

    downloadable = [d for d in deps if isinstance(d.source, GitHubToolSource)]
    for i, dep in enumerate(downloadable, 1):
        if on_progress:
            on_progress(dep.binary_name, i, len(downloadable))
        # Mirrored by ``has_required_tools`` so an unsupported host
        # doesn't loop forever (install skips, check fails, repeat).
        if not dep.is_available_on_host():
            logger.warning(
                "  %s: no release asset for this host (%s/%s); skipping",
                dep.binary_name,
                system,
                platform.machine(),
            )
            continue
        binary_path = bin_dir / f"{dep.binary_name}{exe_suffix()}"
        if binary_path.exists():
            logger.info("  %s: already installed, skipping", dep.binary_name)
            continue
        source = dep.source
        assert isinstance(source, GitHubToolSource)
        asset_name = resolve_native_asset_name(source, suffix or "")
        if asset_name is None:
            # Defensive: should be unreachable via is_available_on_host above.
            logger.warning("  %s: resolve_native_asset_name returned None", dep.binary_name)
            continue
        url = asset_url(source, asset_name)
        compressed = _is_compressed_asset(asset_name)
        # Hash lookup precedence:
        #   1. ``sha256[asset_name]`` — exact-asset pin (works for any asset
        #      including compressed; one entry per arch override).
        #   2. ``sha256[suffix]`` — per-platform pin used by tools we
        #      republish ourselves (tokei, gopls); only consulted for
        #      pre-extracted assets so a stale platform-keyed hash doesn't
        #      get applied to a freshly compressed binary.
        #   3. ``None`` — no pin, ``download_asset`` skips verification.
        if asset_name in source.sha256:
            expected_hash: str | None = source.sha256[asset_name]
        elif not compressed:
            expected_hash = source.sha256.get(suffix or "")
        else:
            expected_hash = None
        try:
            if compressed:
                archive_path = bin_dir / asset_name
                if not download_asset(url, archive_path, expected_sha256=expected_hash):
                    logger.warning("  %s: download failed (empty file)", dep.binary_name)
                    archive_path.unlink(missing_ok=True)
                    continue
                try:
                    _extract_compressed_binary(archive_path, source.archive_inner_path, binary_path)
                finally:
                    archive_path.unlink(missing_ok=True)
            else:
                if not download_asset(url, binary_path, expected_sha256=expected_hash):
                    logger.warning("  %s: download failed (empty file)", dep.binary_name)
                    binary_path.unlink(missing_ok=True)
                    continue
            if system != "Windows":
                os.chmod(binary_path, 0o755)
            logger.info("  %s: downloaded successfully", dep.binary_name)
        except Exception:
            logger.exception("  %s: download failed", dep.binary_name)
            binary_path.unlink(missing_ok=True)


# -- Package-manager installer (dotnet tool, cargo install, ...) --------------


def package_manager_tool_dir(target_dir: Path, dep: ToolDependency) -> Path:
    """Managed install directory for a PACKAGE_MANAGER tool.

    Layout: ``<target_dir>/bin/<platform>/pm-tools/<subdir>/``. Using a
    per-tool subdir keeps multiple pm-tools from clobbering each other.
    """
    subdir = dep.archive_subdir or dep.key
    return platform_bin_dir(target_dir) / "pm-tools" / subdir


def install_package_manager_tools(
    target_dir: Path,
    deps: list[ToolDependency],
    on_progress: ProgressCallback | None = None,
) -> None:
    """Install each dep by invoking its declared package manager.

    Warn-and-skip when the package manager binary is absent — the LSP
    adapter's ``get_lsp_command`` surfaces the actionable error at
    analysis time (mirrors the ``install_node_tools`` npm-missing path).
    """
    pm_deps = [d for d in deps if isinstance(d.source, PackageManagerToolSource)]
    try:
        pm_root = platform_bin_dir(target_dir) / "pm-tools"
    except RuntimeError:
        logger.warning(
            "Unsupported platform %s/%s; skipping package-manager tool installation.",
            platform.system(),
            platform.machine(),
        )
        return
    for i, dep in enumerate(pm_deps, 1):
        if on_progress:
            on_progress(dep.binary_name, i, len(pm_deps))
        source = cast(PackageManagerToolSource, dep.source)
        if not shutil.which(source.manager_binary):
            logger.warning(
                "  %s: %s not found on PATH; skipping install. Users must install it before running analysis.",
                dep.binary_name,
                source.manager_binary,
            )
            continue
        install_dir = pm_root / (dep.archive_subdir or dep.key)
        binary_path = install_dir / f"{dep.binary_name}{exe_suffix()}"
        if binary_path.exists():
            logger.info("  %s: already installed, skipping", dep.binary_name)
            continue
        install_dir.mkdir(parents=True, exist_ok=True)
        args = [arg.format(tool_path=str(install_dir), tag=source.tag) for arg in source.install_args]
        try:
            result = subprocess.run(
                [source.manager_binary, *args],
                capture_output=True,
                text=True,
                check=False,
                timeout=600,
            )
            if result.returncode != 0:
                # A failed install can leave partial files that would
                # make the next run skip install and report a
                # stale binary as healthy.
                shutil.rmtree(install_dir, ignore_errors=True)
                logger.warning(
                    "  %s: %s install failed (exit %d): %s",
                    dep.binary_name,
                    source.manager_binary,
                    result.returncode,
                    (result.stderr or result.stdout)[-500:],
                )
                continue
            if not binary_path.exists():
                shutil.rmtree(install_dir, ignore_errors=True)
                logger.warning(
                    "  %s: %s install reported success but %s is missing",
                    dep.binary_name,
                    source.manager_binary,
                    binary_path,
                )
                continue
            if platform.system() != "Windows":
                os.chmod(binary_path, 0o755)
            logger.info("  %s: installed via %s", dep.binary_name, source.manager_binary)
        except subprocess.TimeoutExpired:
            shutil.rmtree(install_dir, ignore_errors=True)
            logger.warning("  %s: %s install timed out after 600s", dep.binary_name, source.manager_binary)
        except OSError:
            shutil.rmtree(install_dir, ignore_errors=True)
            logger.exception("  %s: %s install could not be invoked", dep.binary_name, source.manager_binary)


# -- Node / npm installer -----------------------------------------------------


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


# -- Archive installer (JDTLS) ------------------------------------------------


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

    url = asset_url(dep.source, "")
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


# -- Top-level install_tools orchestrator -------------------------------------


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
    pm_deps = [d for d in TOOL_REGISTRY if d.kind is ToolKind.PACKAGE_MANAGER]

    if native_deps:
        install_native_tools(target_dir, native_deps)
    if node_deps:
        install_node_tools(target_dir, node_deps)
    for dep in archive_deps:
        install_archive_tool(target_dir, dep)
    if pm_deps:
        install_package_manager_tools(target_dir, pm_deps)


# -- Embedded Node.js runtime bootstrap ---------------------------------------


# Version sentinel written after a successful bootstrap. Contains
# PINNED_NODE_VERSION; a mismatch or absence triggers wipe + reinstall.
NODEENV_VERSION_STAMP = ".codeboarding-node-version"


def embedded_node_is_healthy(base_dir: Path) -> bool:
    """Return True only if the embedded Node binary is genuinely usable.

    Stricter than ``embedded_node_path()``'s existence check: rejects
    zero-byte files, non-executable Unix binaries, and missing version
    sentinels (partial installs from an interrupted previous run).
    """
    node_path_str = embedded_node_path(base_dir)
    if not node_path_str:
        return False

    node_path = Path(node_path_str)
    try:
        if node_path.stat().st_size == 0:
            return False
    except OSError:
        return False

    # Unix only: Windows doesn't use the executable bit.
    if platform.system() != "Windows" and not os.access(node_path, os.X_OK):
        return False

    stamp = nodeenv_root_dir(base_dir) / NODEENV_VERSION_STAMP
    try:
        return stamp.read_text().strip() == PINNED_NODE_VERSION
    except OSError:
        return False


def initialize_nodeenv_globals(nodeenv_module: Any, args: Any) -> None:
    """Replicate the module-global init that nodeenv.main() does before
    calling create_environment().

    nodeenv's ``src_base_url`` defaults to None; calling ``create_environment()``
    in-process (needed inside the frozen wrapper) without this helper produces
    URLs like ``"None/v20.18.1/..."``. Mirrors nodeenv.main()'s init block for
    the prebuilt / non-mirror path.
    """
    nodeenv_module.ignore_ssl_certs = getattr(args, "ignore_ssl_certs", False)

    src_domain: str | None = None
    mirror = getattr(args, "mirror", None)
    if mirror:
        if "://" in mirror:
            nodeenv_module.src_base_url = mirror
            return
        src_domain = mirror
    elif nodeenv_needs_unofficial_builds(nodeenv_module):
        src_domain = "unofficial-builds.nodejs.org"
    else:
        src_domain = "nodejs.org"

    nodeenv_module.src_base_url = f"https://{src_domain}/download/release"


def nodeenv_needs_unofficial_builds(nodeenv_module: Any) -> bool:
    """Return True for musl/riscv64 hosts (nodeenv.main()'s unofficial-builds branch).

    Returns False if a future nodeenv release removes either helper — the
    caller then falls through to nodejs.org, the conservative default.
    """
    is_musl = getattr(nodeenv_module, "is_x86_64_musl", None)
    is_rv64 = getattr(nodeenv_module, "is_riscv64", None)
    try:
        if callable(is_musl) and is_musl():
            return True
        if callable(is_rv64) and is_rv64():
            return True
    except Exception:
        return False
    return False


def install_embedded_node(
    base_dir: Path,
    on_progress: ProgressCallback | None = None,
) -> bool:
    """Download a pinned Node.js runtime into ``<base_dir>/nodeenv/``.

    Idempotent; wipes and reinstalls on unhealthy state.
    """
    if embedded_node_is_healthy(base_dir):
        return True

    env_dir = nodeenv_root_dir(base_dir)

    # Wipe stale state — create_environment() sys.exits on a pre-existing dir.
    if env_dir.exists():
        logger.info("Removing stale/partial embedded Node.js install at %s", env_dir)
        try:
            shutil.rmtree(env_dir)
        except OSError:
            logger.exception("Failed to remove stale embedded Node.js directory; cannot recover")
            return False

    try:
        # Function-level: may be missing in PyInstaller-frozen binaries.
        import nodeenv
    except ImportError:
        logger.exception("nodeenv package is not available; cannot bootstrap Node.js runtime")
        return False

    if on_progress:
        on_progress(f"node-{PINNED_NODE_VERSION}", 1, 1)

    env_dir.parent.mkdir(parents=True, exist_ok=True)

    try:
        # Use make_parser().parse_args(list) directly — nodeenv.parse_args()
        # reads sys.argv, which leaks pytest flags and is wrong in the frozen wrapper.
        parser = nodeenv.make_parser()
        args = parser.parse_args(["--prebuilt", "--node", PINNED_NODE_VERSION, str(env_dir)])
        # Skip nodeenv's config_file probing — it looks at tox.ini / ~/.nodeenvrc
        # which are meaningless here and harmful in a frozen binary without a real HOME.
        args.config_file = []
        # Defensive — keep the source-build path unreachable across nodeenv versions.
        args.prebuilt = True

        # Replicate nodeenv.main()'s module-global init — create_environment()
        # reads nodeenv.src_base_url (defaults to None) when building download URLs.
        initialize_nodeenv_globals(nodeenv, args)

        nodeenv.create_environment(str(env_dir), args)
    except SystemExit:
        # Should be unreachable (we wipe env_dir above), but catch to defend
        # against a race with a concurrent install without the file lock.
        logger.exception("nodeenv.create_environment() called sys.exit — unexpected pre-existing state")
        return False
    except Exception:
        logger.exception("Failed to install embedded Node.js runtime via nodeenv")
        return False

    # Verify before stamping — if nodeenv succeeded but left a bad binary,
    # skip the sentinel so the next run retries.
    node_path_str = embedded_node_path(base_dir)
    if not node_path_str or Path(node_path_str).stat().st_size == 0:
        logger.error("nodeenv.create_environment() completed but node binary is missing or empty")
        return False

    # Stamp atomically via tmp-file swap so a crash mid-write can't leave a partial sentinel.
    stamp = env_dir / NODEENV_VERSION_STAMP
    tmp_stamp = env_dir / f"{NODEENV_VERSION_STAMP}.tmp"
    try:
        tmp_stamp.write_text(PINNED_NODE_VERSION)
        os.replace(tmp_stamp, stamp)
    except OSError:
        logger.exception("Failed to write Node.js version stamp; next run will reinstall")
        return False

    # a previous probe of a broken binary at this same path may have
    # cached None in node_version_tuple's LRU. Without clearing, subsequent
    # preferred_node_path() calls in the same process would skip the freshly
    # installed runtime and fall through to system PATH — ENOENT on Node-less machines.
    node_version_tuple.cache_clear()

    return True
