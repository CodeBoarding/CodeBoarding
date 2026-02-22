import argparse
import io
import os
import platform
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import requests

from tool_registry import (
    TOOL_REGISTRY,
    ToolKind,
    get_servers_dir,
    install_archive_tool,
    install_native_tools,
    install_node_tools,
    platform_bin_dir,
)
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


def check_npm():
    """Check if npm is installed on the system."""
    print("Step: npm check started")

    npm_path = shutil.which("npm")

    if npm_path:
        try:
            result = subprocess.run([npm_path, "--version"], capture_output=True, text=True, check=True)
            print(f"Step: npm check finished: success (version {result.stdout.strip()})")
            return True
        except Exception as e:
            print(
                f"Step: npm check finished: failure - npm command failed ({e}). Skipping Language Servers installation."
            )
            return False
    else:
        print("Step: npm check finished: failure - npm not found")
        return False


def install_npm_with_nodeenv() -> bool:
    """Install npm locally in the active Python virtual environment using nodeenv."""
    command = [sys.executable, "-m", "nodeenv", "--python-virtualenv"]
    print("Step: npm remediation started")
    print(f"   command: {' '.join(command)}")
    print("   impact: installs Node.js + npm into the active Python virtual environment")

    try:
        result = subprocess.run(command, capture_output=True, text=True, check=True)
    except subprocess.CalledProcessError as e:
        print(f"Step: npm remediation finished: failure - {e}")
        if e.stderr:
            print(f"   {e.stderr.strip()}")
        print("   You can install Node.js manually from: https://nodejs.org/en/download")
        print("   Then verify with: npm --version")
        return False

    npm_available = check_npm()
    if not npm_available:
        print("Step: npm remediation finished: failure - npm still not found after nodeenv install")
        print("   You can install Node.js manually from: https://nodejs.org/en/download")
        print("   Then verify with: npm --version")
        return False

    print("Step: npm remediation finished: success")
    return True


def is_non_interactive_mode() -> bool:
    """Captures github actions ("CI) and non-interactive session (no terminal keyboard)"""
    return bool(os.getenv("CI")) or not sys.stdin.isatty()


def resolve_missing_npm(auto_install_npm: bool = False) -> bool:
    """Prompt the user to install npm; abort if they decline or if non-interactive.

    Returns True only when npm becomes available. Raises SystemExit if the user
    declines or if running non-interactively, because npm is required.
    """
    print("Step: npm required for TypeScript/JavaScript/PHP/Python language servers")

    if auto_install_npm:
        installed = install_npm_with_nodeenv()
        if not installed:
            print("Error: npm installation failed. Install Node.js from https://nodejs.org/en/download and retry.")
            raise SystemExit(1)
        return True

    if is_non_interactive_mode():
        print("Error: npm is required but not found and cannot be installed non-interactively.")
        print("   Re-run with --auto-install-npm to install npm in this virtual environment,")
        print("   or install Node.js manually from: https://nodejs.org/en/download")
        raise SystemExit(1)

    choice = input("npm is missing. Install it now using nodeenv in this virtual environment? [y/N]: ").strip().lower()
    if choice in {"y", "yes"}:
        installed = install_npm_with_nodeenv()
        if not installed:
            print("Error: npm installation failed. Install Node.js from https://nodejs.org/en/download and retry.")
            raise SystemExit(1)
        return True

    print("Error: npm is required. Install Node.js from https://nodejs.org/en/download and retry.")
    raise SystemExit(1)


def resolve_npm_availability(auto_install_npm: bool = False) -> bool:
    """Determine npm availability and run remediation when needed."""
    npm_available = check_npm()

    if not npm_available:
        npm_available = resolve_missing_npm(auto_install_npm=auto_install_npm)

    return npm_available


def parse_args() -> argparse.Namespace:
    """Parse install script arguments."""
    parser = argparse.ArgumentParser(description="CodeBoarding installation script")
    parser.add_argument(
        "--auto-install-npm",
        action="store_true",
        help="Automatically install npm via nodeenv when npm is missing",
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


def install_node_servers(target_dir: Path):
    """Install Node.js based servers (TypeScript, Pyright) using npm in target_dir."""
    print("Step: Node.js servers installation started")
    target_dir.mkdir(parents=True, exist_ok=True)

    node_deps = [d for d in TOOL_REGISTRY if d.kind is ToolKind.NODE]
    install_node_tools(target_dir, node_deps)

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


def verify_binary(binary_path: Path) -> bool:
    """Run a quick smoke test to verify the binary actually executes.

    Returns True if the binary runs without DLL-not-found or similar loader errors.
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
            return False
        return True
    except OSError:
        # Binary couldn't be started at all
        return False
    except subprocess.TimeoutExpired:
        # If it ran long enough to time out, it loaded fine
        return True


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

    if auto_install_vcpp:
        return install_vcpp_redistributable()

    if is_non_interactive_mode():
        print("Step: Non-interactive mode detected - skipping VC++ prompt")
        print("   Re-run with --auto-install-vcpp to install automatically")
        print(f"   Or download and install manually from: {VCREDIST_URL}")
        return False

    choice = (
        input("Visual C++ Redistributable is missing. Install it now? (requires admin privileges) [y/N]: ")
        .strip()
        .lower()
    )
    if choice in {"y", "yes"}:
        return install_vcpp_redistributable()

    print("Step: VC++ Redistributable installation skipped by user")
    print(f"   Download and install manually from: {VCREDIST_URL}")
    return False


def download_binaries(target_dir: Path, auto_install_vcpp: bool = False):
    """Download tokei and gopls binaries from the latest GitHub release."""
    print("Step: Binary download started")
    native_deps = [d for d in TOOL_REGISTRY if d.kind is ToolKind.NATIVE]
    install_native_tools(target_dir, native_deps)

    # Verify downloaded binaries actually work (catch missing DLL issues on Windows)
    system = platform.system()
    if system == "Windows":
        platform_bin_dir = get_platform_bin_dir(target_dir)
        needs_vcpp = False
        for dep in native_deps:
            binary_path = platform_bin_dir / f"{dep.binary_name}.exe"
            if not binary_path.exists():
                continue
            if not verify_binary(binary_path):
                print(f"  {dep.binary_name}: verification failed - missing Visual C++ runtime")
                needs_vcpp = True
            else:
                print(f"  {dep.binary_name}: verification passed")

        if needs_vcpp:
            vcpp_resolved = resolve_missing_vcpp(auto_install_vcpp=auto_install_vcpp)
            if vcpp_resolved:
                for dep in native_deps:
                    binary_path = platform_bin_dir / f"{dep.binary_name}.exe"
                    if binary_path.exists() and verify_binary(binary_path):
                        print(f"  {dep.binary_name}: verification passed after VC++ install")

    print("Step: Binary download finished")


def download_jdtls(target_dir: Path):
    """Download and extract JDTLS from the latest GitHub release."""
    print("Step: JDTLS download started")
    archive_deps = [d for d in TOOL_REGISTRY if d.kind is ToolKind.ARCHIVE]
    for dep in archive_deps:
        install_archive_tool(target_dir, dep)

    print("Step: JDTLS download finished")
    return True


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


def print_language_support_summary(npm_available: bool, target_dir: Path):
    """Print which language analyses are currently available based on installed tools."""
    print("Step: Language support summary")

    target_dir = target_dir.resolve()
    platform_bin_dir = get_platform_bin_dir(target_dir)
    is_win = platform.system() == "Windows"
    node_ext = ".cmd" if is_win else ""

    ts_path = target_dir / "node_modules" / ".bin" / f"typescript-language-server{node_ext}"
    php_path = target_dir / "node_modules" / ".bin" / f"intelephense{node_ext}"
    py_node_path = target_dir / "node_modules" / ".bin" / f"pyright-langserver{node_ext}"
    py_env_path = shutil.which("pyright-langserver") or shutil.which("pyright-python-langserver")
    go_path = platform_bin_dir / ("gopls.exe" if is_win else "gopls")
    java_path = target_dir / "bin" / "jdtls"

    npm_missing = "npm not available"
    pyright_missing = "pyright-langserver not found in node_modules or active environment"
    typescript_missing = "typescript-language-server binary not found"

    language_checks: list[LanguageSupportCheck] = [
        LanguageSupportCheck(
            language="Python",
            paths=[py_node_path],
            fallback_available=bool(py_env_path),
            reason_if_requirement_missing=pyright_missing,
            reason_if_binary_missing=pyright_missing,
        ),
        LanguageSupportCheck(
            language="TypeScript",
            paths=[ts_path],
            requires_npm=True,
            reason_if_requirement_missing=npm_missing,
            reason_if_binary_missing=typescript_missing,
        ),
        LanguageSupportCheck(
            language="JavaScript",
            paths=[ts_path],
            requires_npm=True,
            reason_if_requirement_missing=npm_missing,
            reason_if_binary_missing=typescript_missing,
        ),
        LanguageSupportCheck(
            language="PHP",
            paths=[php_path],
            requires_npm=True,
            reason_if_requirement_missing=npm_missing,
            reason_if_binary_missing="intelephense binary not found",
        ),
        LanguageSupportCheck(
            language="Go",
            paths=[go_path],
            reason_if_requirement_missing="gopls binary not found",
            reason_if_binary_missing="gopls binary not found",
        ),
        LanguageSupportCheck(
            language="Java",
            paths=[java_path],
            reason_if_requirement_missing="jdtls installation not found",
            reason_if_binary_missing="jdtls installation not found",
        ),
    ]

    for check in language_checks:
        is_available, reason = check.evaluate(npm_available)
        print(f"  - {check.language}: {'yes' if is_available else 'no'}")
        if reason:
            print(f"    reason: {reason}")


def run_install(
    target_dir: Path | None = None,
    auto_install_npm: bool = False,
    auto_install_vcpp: bool = False,
) -> None:
    """Core installation logic — callable programmatically or via CLI.

    Downloads language server binaries to target_dir (defaults to ~/.codeboarding/servers/).
    Safe to call multiple times; already-installed tools are skipped.
    """
    target = (target_dir or get_servers_dir()).resolve()
    target.mkdir(parents=True, exist_ok=True)

    ensure_config_template()

    npm_available = resolve_npm_availability(auto_install_npm=auto_install_npm)
    if npm_available:
        install_node_servers(target)

    download_binaries(target, auto_install_vcpp=auto_install_vcpp)
    download_jdtls(target)
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

    run_install(auto_install_npm=args.auto_install_npm, auto_install_vcpp=args.auto_install_vcpp)

    from tool_registry import _write_manifest

    _write_manifest()

    print("\n" + "=" * 40)
    print("Setup complete!")
    print("Configure your LLM provider key in ~/.codeboarding/config.toml, then run:")
    print("  codeboarding --local /path/to/repo --project-name MyProject")


if __name__ == "__main__":
    main()
