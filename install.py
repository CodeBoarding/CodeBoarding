import argparse
import io
import os
import platform
import shutil
import subprocess
import sys
import tarfile
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

import requests
from static_analyzer.java_utils import find_java_21_or_later
from tool_registry import (
    PINNED_NODE_VERSION,
    TOOL_REGISTRY,
    ProgressCallback,
    ToolKind,
    acquire_lock,
    get_servers_dir,
    install_archive_tool,
    install_embedded_node,
    install_native_tools,
    install_node_tools,
    install_package_manager_tools,
    needs_install,
    npm_subprocess_env,
    package_manager_tool_path,
    platform_bin_dir,
    preferred_node_path,
    preferred_npm_command,
    write_manifest,
)
from tool_registry.registry import ConfigSection, PackageManagerToolSource
from vscode_constants import VSCODE_CONFIG
from static_analyzer.constants import Language
from user_config import ensure_config_template


@dataclass(frozen=True, slots=True)
class LanguageSupportCheck:
    language: str
    paths: list[Path]
    requires_npm: bool = False
    fallback_available: bool = False
    reason_if_requirement_missing: str = ""
    reason_if_binary_missing: str = ""

    def evaluate(self, npm_available: bool) -> tuple[bool, str | None]:
        requirement_ok = (not self.requires_npm) or npm_available
        path_exists = any(path.exists() for path in self.paths)
        is_available = (path_exists and requirement_ok) or self.fallback_available
        if is_available:
            return True, None

        reason = self.reason_if_requirement_missing if not requirement_ok else self.reason_if_binary_missing
        return False, reason


def check_npm(target_dir: Path | None = None) -> bool:
    """Check if npm is available via the configured Node.js runtime or PATH."""
    print("Step: npm check started")

    target = (target_dir or get_servers_dir()).resolve()
    npm_command = preferred_npm_command(target)

    if npm_command:
        try:
            env = npm_subprocess_env(target)
            result = subprocess.run([*npm_command, "--version"], capture_output=True, text=True, check=True, env=env)
            print(f"Step: npm check finished: success (version {result.stdout.strip()})")
            return True
        except Exception as e:
            print(
                f"Step: npm check finished: failure - npm command failed ({e}). Skipping Language Servers installation."
            )
            return False

    print("Step: npm check finished: failure - npm not found")
    return False


def bootstrapped_npm_cli_path(target_dir: Path) -> Path:
    """Return the npm CLI entrypoint inside the target directory."""
    return target_dir / "npm" / "package" / "bin" / "npm-cli.js"


def extract_tarball_safely(fileobj: io.BytesIO, destination: Path) -> None:
    """Extract a tarball while rejecting path traversal entries."""
    destination = destination.resolve()
    with tarfile.open(fileobj=fileobj, mode="r:gz") as tar:
        for member in tar.getmembers():
            member_path = (destination / member.name).resolve()
            if not member_path.is_relative_to(destination):
                raise ValueError(f"Unsafe tar entry: {member.name}")
        tar.extractall(destination)


def bootstrap_npm(target_dir: Path | None = None) -> bool:
    """Download npm from the registry and invoke it through the configured Node.js runtime."""
    target = (target_dir or get_servers_dir()).resolve()
    target.mkdir(parents=True, exist_ok=True)
    node_path = preferred_node_path(target)

    print("Step: npm remediation started")
    print(f"   target: {target}")
    print("   impact: installs npm into the CodeBoarding tools directory and invokes it via Node.js")

    if not node_path:
        print("Step: npm remediation finished: failure - no Node.js runtime available")
        print("   Set CODEBOARDING_NODE_PATH or install Node.js from: https://nodejs.org/en/download")
        return False

    os.environ.setdefault("CODEBOARDING_NODE_PATH", node_path)
    print(f"   node: {os.environ['CODEBOARDING_NODE_PATH']}")

    npm_cli = bootstrapped_npm_cli_path(target)
    if not npm_cli.exists():
        npm_root = npm_cli.parent.parent.parent
        metadata_url = "https://registry.npmjs.org/npm/latest"
        try:
            metadata_response = requests.get(metadata_url, timeout=30)
            metadata_response.raise_for_status()
            metadata = metadata_response.json()
            tarball_url = metadata["dist"]["tarball"]
            print(f"   source: {tarball_url}")

            tarball_response = requests.get(tarball_url, timeout=60)
            tarball_response.raise_for_status()

            shutil.rmtree(npm_root, ignore_errors=True)
            npm_root.mkdir(parents=True, exist_ok=True)
            extract_tarball_safely(io.BytesIO(tarball_response.content), npm_root)
        except (requests.RequestException, KeyError, OSError, ValueError, tarfile.TarError) as e:
            print(f"Step: npm remediation finished: failure - {e}")
            print("   Install Node.js manually from: https://nodejs.org/en/download")
            print("   Then verify with: npm --version")
            return False

    npm_available = check_npm(target)
    if not npm_available:
        print("Step: npm remediation finished: failure - npm still not found after bootstrap")
        print("   You can install Node.js manually from: https://nodejs.org/en/download")
        print("   Then verify with: npm --version")
        return False

    print("Step: npm remediation finished: success")
    return True


def is_non_interactive_mode() -> bool:
    """Detect environments where interactive prompts are unsafe.

    Covers CI runners, piped stdin, and PyInstaller-bundled executables
    (where stdin reports as a TTY but is not actually connected to a user).
    """
    return bool(os.getenv("CI")) or not sys.stdin.isatty() or getattr(sys, "frozen", False)


def ensure_node_runtime(
    target_dir: Path | None = None,
    auto_install_npm: bool = False,
) -> bool:
    """Ensure a Node.js runtime exists; download a pinned prebuilt if not.

    Idempotent (cheap when Node already resolves via ``preferred_node_path``),
    so safe to call above the ``needs_install()`` short-circuit — otherwise a
    deleted ``~/.codeboarding/servers/nodeenv/`` would never be repaired.

    In non-interactive mode (CI, frozen binary, piped stdin) or when
    ``auto_install_npm`` is set, the y/N prompt is skipped and the bootstrap
    runs automatically. The interactive prompt only gates the download when
    a human is actually attached.
    """
    target = (target_dir or get_servers_dir()).resolve()

    if preferred_node_path(target):
        return True

    print("Step: Node.js runtime required for TypeScript/JavaScript/PHP/Python language servers")

    if not auto_install_npm and not is_non_interactive_mode():
        try:
            choice = (
                input(
                    f"Node.js is missing. Download a pinned runtime (v{PINNED_NODE_VERSION}) "
                    "into ~/.codeboarding/servers/nodeenv/ now? [y/N]: "
                )
                .strip()
                .lower()
            )
        except EOFError:
            choice = "y"

        if choice not in {"y", "yes"}:
            print(
                "Warning: skipping Node.js bootstrap. Node-based language servers "
                "(TypeScript, JavaScript, PHP, Pyright) will be unavailable."
            )
            return False

    print(f"Step: Node.js bootstrap started (pinned version {PINNED_NODE_VERSION})")
    target.mkdir(parents=True, exist_ok=True)
    installed = install_embedded_node(target)

    if not installed:
        print("Warning: Node.js bootstrap failed. Node-based language servers will be unavailable.")
        print("   Install Node.js manually from https://nodejs.org/en/download and retry,")
        print("   or set CODEBOARDING_NODE_PATH to an existing Node.js binary.")
        return False

    print("Step: Node.js bootstrap finished: success")
    return True


def resolve_missing_npm(auto_install_npm: bool = False, target_dir: Path | None = None) -> bool:
    """Try to bootstrap npm; fall back gracefully if it cannot be obtained.

    Returns True when npm becomes available, False otherwise.
    In non-interactive mode (VS Code extension / CI) the function never raises —
    a missing npm just means Node-based language servers will be unavailable.
    """
    print("Step: npm required for TypeScript/JavaScript/PHP/Python language servers")

    if not auto_install_npm and not is_non_interactive_mode():
        try:
            choice = (
                input("npm is missing. Install it now using the configured Node.js runtime? [y/N]: ").strip().lower()
            )
        except EOFError:
            choice = "y"

        if choice not in {"y", "yes"}:
            print("Error: npm is required. Install Node.js from https://nodejs.org/en/download and retry.")
            raise SystemExit(1)

    installed = bootstrap_npm(target_dir=target_dir)
    if not installed:
        print("Warning: npm bootstrap failed. Node-based language servers will be unavailable.")
        print("   Install Node.js from https://nodejs.org/en/download to enable them.")
    return installed


def resolve_npm_availability(auto_install_npm: bool = False, target_dir: Path | None = None) -> bool:
    """Determine npm availability and run remediation when needed."""
    npm_available = check_npm(target_dir)

    if not npm_available:
        npm_available = resolve_missing_npm(auto_install_npm=auto_install_npm, target_dir=target_dir)

    return npm_available


def parse_args() -> argparse.Namespace:
    """Parse install script arguments."""
    parser = argparse.ArgumentParser(description="CodeBoarding installation script")
    parser.add_argument(
        "--auto-install-npm",
        action="store_true",
        help="Automatically bootstrap npm when missing (downloads from registry, runs via Node.js)",
    )
    parser.add_argument(
        "--auto-install-vcpp",
        action="store_true",
        help="Automatically install Visual C++ Redistributable when binaries need it (Windows only)",
    )
    return parser.parse_args()


def get_platform_bin_dir(servers_dir: Path) -> Path:
    """Return static_analyzer/servers/bin/<os> directory."""
    return platform_bin_dir(servers_dir)


def install_node_servers(target_dir: Path, on_progress: ProgressCallback | None = None):
    """Install Node.js based servers (TypeScript, Pyright) using npm in target_dir."""
    print("Step: Node.js servers installation started")
    target_dir.mkdir(parents=True, exist_ok=True)

    node_deps = [d for d in TOOL_REGISTRY if d.kind is ToolKind.NODE]
    install_node_tools(target_dir, node_deps, on_progress=on_progress)

    # Verify the installation
    ts_lsp_path = target_dir / "node_modules" / ".bin" / "typescript-language-server"
    py_lsp_path = target_dir / "node_modules" / ".bin" / "pyright-langserver"
    php_lsp_path = target_dir / "node_modules" / ".bin" / "intelephense"

    success = True
    for name, path in [
        ("TypeScript Language Server", ts_lsp_path),
        ("Pyright Language Server", py_lsp_path),
        ("Intelephense", php_lsp_path),
    ]:
        if path.exists():
            print(f"Step: {name} installation finished: success")
        else:
            print(f"Step: {name} installation finished: warning - Binary not found")
            success = False

    return success


VCREDIST_URL = "https://aka.ms/vs/17/release/vc_redist.x64.exe"
STATUS_DLL_NOT_FOUND = 0xC0000135  # 3221225781 unsigned


class BinaryStatus(StrEnum):
    """Result of a binary verification check."""

    OK = "ok"
    MISSING_VCPP = "missing_vcpp"
    LOAD_ERROR = "load_error"


def verify_binary(binary_path: Path) -> BinaryStatus:
    """Run a quick smoke test to verify the binary actually executes.

    Returns a BinaryStatus constant indicating the result.
    """
    try:
        result = subprocess.run(
            [str(binary_path), "--version"],
            capture_output=True,
            timeout=10,
        )
        # Any exit code is fine as long as the process actually loaded.
        # 0xC0000135 (unsigned 3221225781) means a required DLL is missing.
        if result.returncode < 0:
            code = result.returncode & 0xFFFFFFFF
        else:
            code = result.returncode
        if code == STATUS_DLL_NOT_FOUND:
            return BinaryStatus.MISSING_VCPP
        return BinaryStatus.OK
    except OSError:
        # Binary couldn't be started at all (corrupted, blocked, wrong format)
        return BinaryStatus.LOAD_ERROR
    except subprocess.TimeoutExpired:
        # If it ran long enough to time out, it loaded fine
        return BinaryStatus.OK


def install_vcpp_redistributable() -> bool:
    """Download and install the Visual C++ Redistributable on Windows.

    Required when pre-built binaries are dynamically linked against the MSVC runtime
    (vcruntime140.dll) which is not present on the system.
    """
    if platform.system() != "Windows":
        return False

    print("Step: Visual C++ Redistributable installation started")
    print("  The downloaded binary requires the Visual C++ runtime (vcruntime140.dll).")

    installer_path = Path("static_analyzer/servers/bin/vc_redist.x64.exe")
    installer_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        print("  Downloading VC++ Redistributable...")
        response = requests.get(VCREDIST_URL, stream=True, timeout=120, allow_redirects=True)
        response.raise_for_status()
        with open(installer_path, "wb") as f:
            for chunk in response.iter_content(chunk_size=32768):
                if chunk:
                    f.write(chunk)

        print("  Running installer (this will request administrator privileges)...")
        # Use PowerShell Start-Process with -Verb RunAs to trigger UAC elevation,
        # then /install /passive for a non-interactive install with progress bar.
        ps_command = (
            f'Start-Process -FilePath "{installer_path.resolve()}" '
            f'-ArgumentList "/install","/passive","/norestart" '
            f"-Verb RunAs -Wait"
        )
        result = subprocess.run(
            ["powershell", "-Command", ps_command],
            check=False,
            timeout=300,
        )

        try:
            installer_path.unlink(missing_ok=True)
        except PermissionError:
            pass  # Installer may still be releasing; not critical

        if result.returncode == 0:
            print("Step: Visual C++ Redistributable installation finished: success")
            return True
        elif result.returncode == 1638:
            # 1638 = newer version already installed
            print("Step: Visual C++ Redistributable installation finished: success (newer version already present)")
            return True
        elif result.returncode == 3010:
            # 3010 = success, reboot required
            print("Step: Visual C++ Redistributable installation finished: success (reboot may be needed)")
            return True
        else:
            print(f"Step: Visual C++ Redistributable installation finished: failure (exit code {result.returncode})")
            print("  You may need to run the installer manually with administrator privileges.")
            print(f"  Download from: {VCREDIST_URL}")
            return False

    except Exception as e:
        try:
            installer_path.unlink(missing_ok=True)
        except PermissionError:
            pass
        print(f"Step: Visual C++ Redistributable installation finished: failure - {e}")
        print(f"  Download and install manually from: {VCREDIST_URL}")
        return False


def resolve_missing_vcpp(auto_install_vcpp: bool = False) -> bool:
    """Offer actionable paths when the Visual C++ Redistributable is missing."""
    print("Step: Visual C++ Redistributable required for downloaded binaries (vcruntime140.dll)")

    if auto_install_vcpp or is_non_interactive_mode():
        return install_vcpp_redistributable()

    try:
        choice = (
            input("Visual C++ Redistributable is missing. Install it now? (requires admin privileges) [y/N]: ")
            .strip()
            .lower()
        )
    except EOFError:
        choice = "y"

    if choice in {"y", "yes"}:
        return install_vcpp_redistributable()

    print("Step: VC++ Redistributable installation skipped by user")
    print(f"   Download and install manually from: {VCREDIST_URL}")
    return False


def download_binaries(target_dir: Path, auto_install_vcpp: bool = False, on_progress: ProgressCallback | None = None):
    """Download tokei and gopls binaries from the latest GitHub release."""
    print("Step: Binary download started")
    native_deps = [d for d in TOOL_REGISTRY if d.kind is ToolKind.NATIVE]
    install_native_tools(target_dir, native_deps, on_progress=on_progress)

    # Verify downloaded binaries actually work (catch missing DLL issues on Windows)
    if platform.system() == "Windows":
        bin_dir = get_platform_bin_dir(target_dir)
        vcpp_failures: list[str] = []
        load_errors: list[str] = []
        for dep in native_deps:
            binary_path = bin_dir / f"{dep.binary_name}.exe"
            if not binary_path.exists():
                continue
            status = verify_binary(binary_path)
            if status == BinaryStatus.OK:
                print(f"  {dep.binary_name}: verification passed")
            elif status == BinaryStatus.MISSING_VCPP:
                print(f"  {dep.binary_name}: verification failed - missing Visual C++ runtime")
                vcpp_failures.append(dep.binary_name)
            else:
                print(f"  {dep.binary_name}: verification failed - binary could not be loaded")
                print(f"    The binary may be corrupted or blocked by antivirus software.")
                print(f"    Try deleting {binary_path} and re-running setup.")
                load_errors.append(dep.binary_name)

        if vcpp_failures and resolve_missing_vcpp(auto_install_vcpp=auto_install_vcpp):
            for name in vcpp_failures:
                binary_path = bin_dir / f"{name}.exe"
                if verify_binary(binary_path) == BinaryStatus.OK:
                    print(f"  {name}: verification passed after VC++ install")

        if load_errors:
            print(
                f"  Warning: {', '.join(load_errors)} could not be loaded. "
                f"Language support for those tools will be unavailable."
            )

    print("Step: Binary download finished")


def download_jdtls(target_dir: Path, on_progress: ProgressCallback | None = None):
    """Download and extract JDTLS from the latest GitHub release."""
    print("Step: JDTLS download started")
    archive_deps = [d for d in TOOL_REGISTRY if d.kind is ToolKind.ARCHIVE]
    for dep in archive_deps:
        install_archive_tool(target_dir, dep, on_progress=on_progress)

    print("Step: JDTLS download finished")
    return True


def install_package_manager_lsp_servers(target_dir: Path, on_progress: ProgressCallback | None = None) -> None:
    """Install LSP servers distributed via user-provided package managers.

    Skips cleanly when the package manager itself is absent — the adapter raises a meaningful error
    at analysis time.
    """
    pm_deps = [d for d in TOOL_REGISTRY if d.kind is ToolKind.PACKAGE_MANAGER]
    if not pm_deps:
        return
    print("Step: Package-manager tool installation started")
    install_package_manager_tools(target_dir, pm_deps, on_progress=on_progress)
    for dep in pm_deps:
        binary_path = package_manager_tool_path(target_dir, dep)
        if binary_path is None:
            print(f"  {dep.binary_name}: not installed (unsupported platform)")
            continue
        if binary_path.exists():
            print(f"  {dep.binary_name}: installed")
        else:
            manager = (
                dep.source.manager_binary if isinstance(dep.source, PackageManagerToolSource) else "package manager"
            )
            print(f"  {dep.binary_name}: not installed ({manager} unavailable or install failed)")
    print("Step: Package-manager tool installation finished")


def install_pre_commit_hooks():
    """Install pre-commit hooks for code formatting and linting (optional for contributors)."""
    pre_commit_config = Path(".pre-commit-config.yaml")
    if not pre_commit_config.exists():
        return

    try:
        # Check if pre-commit is installed (only available with dev dependencies)
        result = subprocess.run(
            [sys.executable, "-m", "pre_commit", "--version"],
            capture_output=True,
            text=True,
            check=False,
        )

        if result.returncode != 0:
            # Pre-commit not installed - this is fine for regular users
            return

        print("Step: pre-commit hooks installation started")

        # Install pre-commit hooks
        subprocess.run(
            [sys.executable, "-m", "pre_commit", "install"],
            check=True,
            capture_output=True,
            text=True,
        )
        print("Step: pre-commit hooks installation finished: success")

    except subprocess.CalledProcessError:
        # Silently skip if installation fails
        pass
    except Exception:
        # Silently skip if any other error occurs
        pass


def _language_checks_from_registry(target_dir: Path) -> list[LanguageSupportCheck]:
    """Build one LanguageSupportCheck per LSP language listed in VSCODE_CONFIG.

    Data-driven so adding a language to TOOL_REGISTRY + VSCODE_CONFIG
    automatically flows to the setup summary — no second list to keep in sync.
    Each tool dep can surface multiple display languages (e.g. the
    ``typescript`` entry covers both TypeScript and JavaScript).
    """
    target_dir = target_dir.resolve()
    # native/package-manager installers already warn-and-skip on unsupported
    # hosts; the summary should mirror that instead of crashing when the host has
    # no ``platform_bin_dir`` layout.
    try:
        platform_bin_dir: Path | None = get_platform_bin_dir(target_dir)
    except RuntimeError:
        platform_bin_dir = None
    is_win = platform.system() == "Windows"
    node_ext = ".cmd" if is_win else ""
    native_ext = ".exe" if is_win else ""
    npm_missing = "npm not available"

    checks: list[LanguageSupportCheck] = []
    lsp_servers = VSCODE_CONFIG.get("lsp_servers", {})

    for dep in TOOL_REGISTRY:
        if dep.config_section != ConfigSection.LSP_SERVERS:
            continue
        config_entry = lsp_servers.get(dep.key)
        if config_entry is None:
            continue
        languages: list[str] = list(config_entry.get("languages", []) or [dep.key])

        paths: list[Path] = []
        fallback_available = False
        requires_npm = False
        reason_requirement = f"{dep.binary_name} not installed"
        reason_binary = f"{dep.binary_name} binary not found"

        if dep.kind is ToolKind.NATIVE:
            if platform_bin_dir is not None:
                paths.append(platform_bin_dir / f"{dep.binary_name}{native_ext}")
            else:
                reason_requirement = f"{dep.binary_name} unavailable on this platform"
                reason_binary = reason_requirement
        elif dep.kind is ToolKind.NODE:
            requires_npm = True
            reason_requirement = npm_missing
            paths.append(target_dir / "node_modules" / ".bin" / f"{dep.binary_name}{node_ext}")
            # Python pyright also resolves from the active environment.
            if dep.key == "python":
                env_path = shutil.which("pyright-langserver") or shutil.which("pyright-python-langserver")
                fallback_available = bool(env_path)
                reason_requirement = "pyright-langserver not found in node_modules or active environment"
                reason_binary = reason_requirement
        elif dep.kind is ToolKind.ARCHIVE:
            # JDTLS is validated by directory presence (+ plugins/ subdir),
            # mirroring has_required_tools.
            subdir = dep.archive_subdir or dep.key
            paths.append(target_dir / "bin" / subdir)
            reason_requirement = f"{subdir} installation not found"
            reason_binary = reason_requirement
            # Java analysis can still proceed when a system Java 21+ is available
            # (jdtls's bundled JRE is the default, but not strictly required).
            if dep.key == "java":
                fallback_available = bool(find_java_21_or_later())
                reason_binary = "jdtls or Java 21+ not found"
        elif dep.kind is ToolKind.PACKAGE_MANAGER:
            pm_path = package_manager_tool_path(target_dir, dep)
            if pm_path is not None:
                paths.append(pm_path)
            manager = (
                dep.source.manager_binary if isinstance(dep.source, PackageManagerToolSource) else "package manager"
            )
            if pm_path is None:
                reason_requirement = f"{dep.binary_name} unavailable on this platform"
            else:
                reason_requirement = f"{dep.binary_name} not installed ({manager} unavailable or install failed)"
            reason_binary = reason_requirement

        for lang in languages:
            checks.append(
                LanguageSupportCheck(
                    language=Language(lang).value,
                    paths=list(paths),
                    requires_npm=requires_npm,
                    fallback_available=fallback_available,
                    reason_if_requirement_missing=reason_requirement,
                    reason_if_binary_missing=reason_binary,
                )
            )
    return checks


def print_language_support_summary(npm_available: bool, target_dir: Path):
    """Print which language analyses are currently available based on installed tools."""
    print("Step: Language support summary")
    for check in _language_checks_from_registry(target_dir):
        is_available, reason = check.evaluate(npm_available)
        print(f"  - {check.language}: {'yes' if is_available else 'no'}")
        if reason:
            print(f"    reason: {reason}")


def ensure_tools(
    auto_install_npm: bool = False,
    auto_install_vcpp: bool = False,
    on_progress: ProgressCallback | None = None,
) -> None:
    """Install tools to ~/.codeboarding/servers/ if needed. No-op if already current.

    Uses a file lock so that concurrent instances (multiple VSCode windows)
    don't corrupt binaries by downloading simultaneously.

    Args:
        on_progress: Optional callback invoked as (tool_name, step, total) during downloads.
    """
    servers_dir = get_servers_dir()
    servers_dir.mkdir(parents=True, exist_ok=True)
    lock_path = servers_dir / ".download.lock"

    with open(lock_path, "w") as lock_fd:
        acquire_lock(lock_fd)

        # Run above needs_install() so a deleted nodeenv/ is always repaired
        # (fingerprints alone wouldn't detect it).
        ensure_node_runtime(target_dir=servers_dir, auto_install_npm=auto_install_npm)

        if not needs_install():
            return

        run_install(
            target_dir=servers_dir,
            auto_install_npm=auto_install_npm,
            auto_install_vcpp=auto_install_vcpp,
            on_progress=on_progress,
        )
        write_manifest()


def run_install(
    target_dir: Path | None = None,
    auto_install_npm: bool = False,
    auto_install_vcpp: bool = False,
    on_progress: ProgressCallback | None = None,
) -> None:
    """Core installation logic — callable programmatically or via CLI.

    Downloads language server binaries to target_dir (defaults to ~/.codeboarding/servers/).
    Safe to call multiple times; already-installed tools are skipped.

    The ``on_progress`` callback receives ``(tool_name, step, total)`` where
    step/total count across *all* tool categories (native, node, archive).
    """
    target = (target_dir or get_servers_dir()).resolve()
    target.mkdir(parents=True, exist_ok=True)

    ensure_config_template()

    # Covers the codeboarding-setup -> run_install path, which bypasses ensure_tools().
    ensure_node_runtime(target_dir=target, auto_install_npm=auto_install_npm)

    # Compute a unified total so the caller sees a single progress stream.
    native_count = sum(1 for d in TOOL_REGISTRY if d.kind is ToolKind.NATIVE and d.source)
    node_deps = [d for d in TOOL_REGISTRY if d.kind is ToolKind.NODE]
    archive_count = sum(1 for d in TOOL_REGISTRY if d.kind is ToolKind.ARCHIVE)
    pm_count = sum(1 for d in TOOL_REGISTRY if d.kind is ToolKind.PACKAGE_MANAGER)
    npm_available = resolve_npm_availability(auto_install_npm=auto_install_npm, target_dir=target)
    total_steps = native_count + (1 if npm_available and node_deps else 0) + archive_count + pm_count

    step = 0

    def unified_progress(name: str, _i: int, _t: int) -> None:
        nonlocal step
        step += 1
        if on_progress:
            on_progress(name, step, total_steps)

    tracker = unified_progress if on_progress else None

    if npm_available:
        install_node_servers(target, on_progress=tracker)

    download_binaries(target, auto_install_vcpp=auto_install_vcpp, on_progress=tracker)
    download_jdtls(target, on_progress=tracker)
    install_package_manager_lsp_servers(target, on_progress=tracker)
    install_pre_commit_hooks()
    print_language_support_summary(npm_available, target)


def main() -> None:
    """Entry point for the `codeboarding-setup` CLI command."""
    # Windows consoles default to cp1252 which can't encode emojis; force UTF-8.
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

    args = parse_args()

    print("CodeBoarding Setup")
    print("=" * 40)

    # Lock at the entry point, not inside run_install() — ensure_tools()
    # calls run_install() while already holding this lock, and same-process
    # reentrant acquisition isn't portable across fcntl / msvcrt.
    servers_dir = get_servers_dir()
    servers_dir.mkdir(parents=True, exist_ok=True)
    lock_path = servers_dir / ".download.lock"
    with open(lock_path, "w") as lock_fd:
        acquire_lock(lock_fd)
        run_install(auto_install_npm=args.auto_install_npm, auto_install_vcpp=args.auto_install_vcpp)
        write_manifest()

    print("\n" + "=" * 40)
    print("Setup complete!")
    print("Configure your LLM provider key in ~/.codeboarding/config.toml, then run:")
    print("  codeboarding full --local /path/to/repo")


if __name__ == "__main__":
    main()
