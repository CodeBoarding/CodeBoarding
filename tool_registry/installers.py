"""Tool installers: download, npm install, archive extract, and embedded Node bootstrap.

This is the only module in the package that performs side effects on the
user's filesystem beyond reading it.  Everything that writes to
``~/.codeboarding/servers/`` — pulling down native binaries from GitHub
releases, running ``npm install``, extracting JDTLS, and bootstrapping a
pinned Node.js runtime via the ``nodeenv`` package — lives here.

Kept separate from ``paths`` and ``manifest`` so those layers stay free of
subprocess/network side effects and can be imported cheaply at startup.
"""

import hashlib
import logging
import os
import platform
import shutil
import subprocess
import tarfile
from pathlib import Path
from typing import Any

import requests

from .paths import (
    embedded_node_path,
    exe_suffix,
    nodeenv_root_dir,
    npm_subprocess_env,
    platform_bin_dir,
    preferred_npm_command,
)

logger = logging.getLogger(__name__)


# -- Download primitive -------------------------------------------------------


def asset_url(source: Any, asset_name: str) -> str:
    """Construct the download URL for a tool asset.

    For GitHub-hosted tools, builds a releases URL from repo/tag/asset.
    For upstream sources, the url_template is the full URL with a ``{version}`` placeholder.
    """
    from . import GitHubToolSource, UpstreamToolSource

    if isinstance(source, UpstreamToolSource):
        return source.url_template.format(version=source.tag, build=source.build)
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


# -- Native binary installer --------------------------------------------------


def install_native_tools(
    target_dir: Path,
    deps: "list",
    on_progress: Any = None,
) -> None:
    """Download native binaries from their configured sources."""
    from . import _PLATFORM_SUFFIX, GitHubToolSource

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
        url = asset_url(source, asset_name)
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


# -- Node / npm installer -----------------------------------------------------


def install_node_tools(
    target_dir: Path,
    deps: "list",
    on_progress: Any = None,
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
    dep: Any,
    on_progress: Any = None,
) -> None:
    """Download and extract an archive tool."""
    from . import GitHubToolSource

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
    from . import TOOL_REGISTRY, ToolKind

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


# -- Embedded Node.js runtime bootstrap ---------------------------------------


# Sentinel file written next to the nodeenv install after a successful bootstrap.
# Contains PINNED_NODE_VERSION. Used on subsequent runs to decide whether an
# existing install is (a) complete and (b) the version we currently pin.
# If missing or mismatched, install_embedded_node() wipes and reinstalls.
NODEENV_VERSION_STAMP = ".codeboarding-node-version"


def embedded_node_is_healthy(base_dir: Path) -> bool:
    """Return True only if the embedded Node binary looks genuinely usable.

    Stricter than ``embedded_node_path()`` (which just calls ``exists()``).
    Catches three real partial-install failure modes that the plain existence
    check would accept:

        1. Zero-byte ``node`` file — disk full mid-extract, or truncated write.
        2. Non-executable ``node`` on Unix — ``chmod +x`` step failed.
        3. Missing sentinel — a prior install crashed after creating the binary
           but before stamping the version, so we can't trust it's the pin.

    Kept separate from ``embedded_node_path()`` so the rest of the codebase
    (LSP command resolution, ``has_required_tools``) keeps its simpler
    "does the path exist" semantics — we only need the stricter check to
    decide whether ``install_embedded_node`` should reinstall.
    """
    from . import PINNED_NODE_VERSION

    node_path_str = embedded_node_path(base_dir)
    if not node_path_str:
        return False

    node_path = Path(node_path_str)
    try:
        if node_path.stat().st_size == 0:
            return False
    except OSError:
        return False

    # On Unix, the binary must be executable. Windows doesn't use the
    # executable bit — the .exe suffix is what matters and that's already
    # baked into embedded_node_path().
    if platform.system() != "Windows" and not os.access(node_path, os.X_OK):
        return False

    stamp = nodeenv_root_dir(base_dir) / NODEENV_VERSION_STAMP
    try:
        return stamp.read_text().strip() == PINNED_NODE_VERSION
    except OSError:
        return False


def initialize_nodeenv_globals(nodeenv_module: Any, args: Any) -> None:
    """Replicate the module-global initialization that nodeenv.main() does.

    nodeenv is designed to be invoked via its CLI entry point ``main()``,
    which sets module-level globals ``src_base_url`` and ``ignore_ssl_certs``
    based on argparse results before calling ``create_environment()``.  When
    we bypass ``main()`` and call ``create_environment()`` in-process (which
    we need to do inside the PyInstaller-frozen wrapper), those globals stay
    at their defaults — ``src_base_url`` is ``None`` — and downstream URL
    construction produces strings like ``"None/v20.18.1/..."`` that crash
    with ``ValueError: unknown url type``.

    This helper mirrors the relevant init block from nodeenv.main() at
    lines ~1116-1133 of nodeenv.py.  We only handle the ``prebuilt`` /
    non-``--mirror`` path that install_embedded_node() actually exercises;
    musl and riscv64 are detected via nodeenv's own helpers so we stay
    correct on Alpine / RISC-V hosts.
    """
    nodeenv_module.ignore_ssl_certs = getattr(args, "ignore_ssl_certs", False)

    # Mirror the src_base_url resolution from nodeenv.main().  We do NOT
    # support --mirror in our args, so args.mirror is always None here.
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
    """Return True on hosts where nodeenv would use unofficial Node builds.

    Matches the ``elif is_x86_64_musl() or is_riscv64()`` branch of
    nodeenv.main().  Split out so ``initialize_nodeenv_globals`` stays
    easy to read and so the musl/riscv detection can be tested in isolation.
    Returns False if either helper is missing on a future nodeenv release,
    so the caller falls through to the standard ``nodejs.org`` host — which
    is the conservative default for unknown platforms.
    """
    is_musl = getattr(nodeenv_module, "is_x86_64_musl", None)
    is_rv64 = getattr(nodeenv_module, "is_riscv64", None)
    try:
        if callable(is_musl) and is_musl():
            return True
        if callable(is_rv64) and is_rv64():
            return True
    except Exception:
        # If either helper raises on an exotic platform, don't let that
        # block the bootstrap — fall through to nodejs.org and let the
        # download attempt surface any real incompatibility.
        return False
    return False


def install_embedded_node(
    base_dir: Path,
    on_progress: Any = None,
) -> bool:
    """Download a pinned Node.js runtime into ``<base_dir>/nodeenv/``.

    Used when the user has no system Node.js and has not set
    ``CODEBOARDING_NODE_PATH``.  Calls ``nodeenv.create_environment()``
    in-process — **not** via ``python -m nodeenv`` — so this also works
    inside the PyInstaller-frozen wrapper binary, where ``sys.executable``
    points at the frozen binary rather than a Python interpreter.

    Always runs in prebuilt mode so the source-build path (which would
    shell out to ``python2`` and a C compiler) is never reached.

    Idempotent and recovery-aware: returns ``True`` immediately when an
    existing install is healthy (non-empty executable binary + matching
    version stamp).  When an install directory exists but is *unhealthy*
    (partial download from a previous interrupted run, zero-byte binary,
    or an older pinned version) the stale directory is wiped and reinstalled.
    This matters because ``nodeenv.create_environment()`` hard-exits with
    ``SystemExit(2)`` when its target directory already exists — that
    ``SystemExit`` would bypass any ``except Exception`` and kill the whole
    process, including a PyInstaller-frozen wrapper.

    Returns ``True`` when the runtime is available after the call,
    ``False`` on failure (which callers surface as "Node-based language
    servers will be unavailable" rather than a hard error).
    """
    from . import PINNED_NODE_VERSION

    if embedded_node_is_healthy(base_dir):
        return True

    env_dir = nodeenv_root_dir(base_dir)

    # Wipe any partial/stale state before calling create_environment() —
    # it hard-exits (sys.exit(2)) on a pre-existing directory, which is
    # not catchable by ``except Exception``.
    if env_dir.exists():
        logger.info("Removing stale/partial embedded Node.js install at %s", env_dir)
        try:
            shutil.rmtree(env_dir)
        except OSError:
            logger.exception("Failed to remove stale embedded Node.js directory; cannot recover")
            return False

    try:
        import nodeenv  # local import keeps cold-path imports out of the hot path
    except ImportError:
        logger.exception("nodeenv package is not available; cannot bootstrap Node.js runtime")
        return False

    if on_progress:
        on_progress(f"node-{PINNED_NODE_VERSION}", 1, 1)

    env_dir.parent.mkdir(parents=True, exist_ok=True)

    try:
        # Build the argparse Namespace directly from nodeenv's own parser.
        # We deliberately bypass ``nodeenv.parse_args()`` because that helper
        # reads ``sys.argv`` rather than accepting an argv list — which would
        # crash under pytest (args like ``--no-header`` leak in) and is also
        # unsafe inside the PyInstaller-frozen wrapper where ``sys.argv`` is
        # the wrapper's own CLI.  ``make_parser()`` gives us the raw parser;
        # calling ``.parse_args(list)`` on it returns a Namespace without
        # touching global state.
        parser = nodeenv.make_parser()
        args = parser.parse_args(
            [
                "--prebuilt",
                "--node",
                PINNED_NODE_VERSION,
                str(env_dir),
            ]
        )
        # nodeenv.parse_args() normally post-processes ``config_file``: when
        # None, it probes ./tox.ini, ./setup.cfg, and ~/.nodeenvrc.  Those
        # paths are meaningless in our install directory and actively harmful
        # in a frozen binary without a real HOME, so we skip config entirely.
        args.config_file = []
        # Defensive: these flags are already set by parse_args() from the
        # argv list above, but if a future nodeenv release changes the
        # default we want the source-build path to stay unreachable — it
        # would try to invoke python2 and a C compiler, neither of which we
        # can rely on inside the frozen wrapper.
        args.prebuilt = True

        # Initialize nodeenv's module-level globals the same way its own
        # main() does at lines ~1116-1133 of nodeenv.py.  create_environment()
        # reads ``nodeenv.src_base_url`` (a module-level global!) to build
        # download URLs, and that global is None by default — so calling
        # create_environment() without running main() first produces URLs
        # like "None/v20.18.1/node-v20.18.1-linux-x64.tar.gz" and crashes
        # with ``ValueError: unknown url type``.  We replicate main()'s
        # initialization in-process to fix this.
        initialize_nodeenv_globals(nodeenv, args)

        nodeenv.create_environment(str(env_dir), args)
    except SystemExit:
        # create_environment() calls sys.exit(2) when env_dir already exists.
        # We wipe env_dir above so this *should* be unreachable, but if
        # something races us into that state (concurrent install without
        # the file lock), catching SystemExit keeps the wrapper process alive
        # instead of dying with an uncatchable exit.
        logger.exception("nodeenv.create_environment() called sys.exit — unexpected pre-existing state")
        return False
    except Exception:
        logger.exception("Failed to install embedded Node.js runtime via nodeenv")
        return False

    # Verify the install actually produced a usable binary before stamping.
    # If nodeenv reported success but the binary is missing/empty, we do
    # *not* write the sentinel — so the next run will detect the broken
    # state and retry rather than trusting it.
    node_path_str = embedded_node_path(base_dir)
    if not node_path_str or Path(node_path_str).stat().st_size == 0:
        logger.error("nodeenv.create_environment() completed but node binary is missing or empty")
        return False

    # Stamp the version last so the sentinel only exists when the install
    # is actually healthy. Written atomically via a temp-file swap to avoid
    # leaving a partial sentinel if we're interrupted during the write.
    stamp = env_dir / NODEENV_VERSION_STAMP
    tmp_stamp = env_dir / f"{NODEENV_VERSION_STAMP}.tmp"
    try:
        tmp_stamp.write_text(PINNED_NODE_VERSION)
        os.replace(tmp_stamp, stamp)
    except OSError:
        logger.exception("Failed to write Node.js version stamp; next run will reinstall")
        return False

    return True
