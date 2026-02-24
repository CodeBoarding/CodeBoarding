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
import yaml

from tool_registry import (
    TOOL_REGISTRY,
    ToolKind,
    install_archive_tool,
    install_native_tools,
    install_node_tools,
    platform_bin_dir,
)


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


def check_uv_environment():
    """Validate that we're running within a uv virtual environment."""
    print("Step: Environment validation started")

    # Check if we're in a virtual environment
    if not hasattr(sys, "base_prefix") or sys.base_prefix == sys.prefix:
        print("Step: Environment validation finished: failure - Not in virtual environment")
        print("Please create and activate a uv environment first:")
        print("  uv venv")
        print("  source .venv/bin/activate  # On Unix/Mac")
        print("  .venv\\Scripts\\activate     # On Windows")
        sys.exit(1)

    # Check if it's specifically a uv environment
    venv_path = Path(sys.prefix)
    uv_marker = venv_path / "pyvenv.cfg"

    if uv_marker.exists():
        with open(uv_marker, "r") as f:
            content = f.read()
            if "uv" not in content.lower():
                print("Step: Environment validation finished: warning - May not be uv environment")

    print("Step: Environment validation finished: success")


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
    """Offer actionable paths when npm is missing."""
    print("Step: npm required for TypeScript/JavaScript/PHP language servers")

    if auto_install_npm:
        return install_npm_with_nodeenv()

    if is_non_interactive_mode():
        print("Step: Non-interactive mode detected - skipping npm prompt")
        print("   Re-run with --auto-install-npm to install npm in this virtual environment")
        print("   Or install Node.js manually from: https://nodejs.org/en/download")
        print("   Then verify with: npm --version")
        return False

    choice = input("npm is missing. Install it now using nodeenv in this virtual environment? [y/N]: ").strip().lower()
    if choice in {"y", "yes"}:
        return install_npm_with_nodeenv()

    print("Step: npm remediation skipped by user")
    print("   Install Node.js manually from: https://nodejs.org/en/download")
    print("   Then verify with: npm --version")
    return False


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


def install_node_servers():
    """Install Node.js based servers (TypeScript, Pyright) using npm in the servers directory."""
    print("Step: Node.js servers installation started")
    servers_dir = Path("static_analyzer/servers")
    servers_dir.mkdir(parents=True, exist_ok=True)

    node_deps = [d for d in TOOL_REGISTRY if d.kind is ToolKind.NODE]
    install_node_tools(servers_dir, node_deps)

    # Verify the installation
    ts_lsp_path = servers_dir / "node_modules" / ".bin" / "typescript-language-server"
    py_lsp_path = servers_dir / "node_modules" / ".bin" / "pyright-langserver"
    php_lsp_path = servers_dir / "node_modules" / ".bin" / "intelephense"

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


def download_binaries(auto_install_vcpp: bool = False):
    """Download tokei and gopls binaries from the latest GitHub release."""
    print("Step: Binary download started")
    servers_dir = Path("static_analyzer/servers")
    native_deps = [d for d in TOOL_REGISTRY if d.kind is ToolKind.NATIVE]
    install_native_tools(servers_dir, native_deps)

    # Verify downloaded binaries actually work (catch missing DLL issues on Windows)
    system = platform.system()
    if system == "Windows":
        platform_bin_dir = get_platform_bin_dir(servers_dir)
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


def download_jdtls():
    """Download and extract JDTLS from the latest GitHub release."""
    print("Step: JDTLS download started")
    servers_dir = Path("static_analyzer/servers")
    archive_deps = [d for d in TOOL_REGISTRY if d.kind is ToolKind.ARCHIVE]
    for dep in archive_deps:
        install_archive_tool(servers_dir, dep)

    print("Step: JDTLS download finished")
    return True


def update_static_analysis_config():
    """Update static_analysis_config.yml with correct paths to binaries.

    Iterates the TOOL_REGISTRY to resolve binary paths under static_analyzer/servers/,
    then writes the updated config back to disk.
    """
    print("Step: Configuration update started")

    config_path = Path("static_analysis_config.yml")
    if not config_path.exists():
        print("Step: Configuration update finished: failure - static_analysis_config.yml not found")
        return

    with open(config_path, "r") as f:
        config = yaml.safe_load(f)

    project_root = Path.cwd().resolve()
    servers_dir = project_root / "static_analyzer" / "servers"
    bin_dir = platform_bin_dir(servers_dir)
    is_win = platform.system() == "Windows"
    native_ext = ".exe" if is_win else ""
    node_ext = ".cmd" if is_win else ""

    updates = 0

    for dep in TOOL_REGISTRY:
        section = config.get(dep.config_section, {})
        entry = section.get(dep.key)
        if entry is None:
            continue

        if dep.kind is ToolKind.NATIVE:
            full_path = bin_dir / f"{dep.binary_name}{native_ext}"
            if full_path.exists():
                entry["command"][0] = str(full_path)
                updates += 1

        elif dep.kind is ToolKind.NODE:
            full_path = servers_dir / "node_modules" / ".bin" / f"{dep.binary_name}{node_ext}"
            if full_path.exists():
                entry["command"][0] = str(full_path)
                updates += 1

        elif dep.kind is ToolKind.ARCHIVE and dep.archive_subdir:
            archive_dir = servers_dir / "bin" / dep.archive_subdir
            if archive_dir.is_dir():
                entry["jdtls_root"] = str(archive_dir)
                updates += 1

    # Fallback: if pyright wasn't installed under node_modules, try the active environment
    pyright_node = servers_dir / "node_modules" / ".bin" / f"pyright-langserver{node_ext}"
    if not pyright_node.exists():
        env_pyright = shutil.which("pyright-langserver") or shutil.which("pyright-python-langserver")
        if env_pyright and "lsp_servers" in config and "python" in config["lsp_servers"]:
            config["lsp_servers"]["python"]["command"][0] = env_pyright
            updates += 1

    with open(config_path, "w") as f:
        yaml.safe_dump(config, f, default_flow_style=False, sort_keys=False)

    print(f"Step: Configuration update finished: success ({updates} paths updated)")


def init_dot_env_file():
    """Initialize .env file with default configuration and commented examples."""
    print("Step: .env file creation started")

    env_file_path = Path(".env")
    if env_file_path.exists():
        print("Step: .env file creation finished: skipped - .env already exists")
        return

    # Get the absolute path to the project root
    project_root = Path.cwd().resolve()

    # Environment variables content
    env_content = f"""# CodeBoarding Environment Configuration
# Generated by setup.py

# ============================================================================
# ACTIVE CONFIGURATION
# ============================================================================

# LLM Provider Configuration (uncomment and configure one)
OLLAMA_BASE_URL=http://localhost:11434

# Core Configuration
REPO_ROOT={project_root}/repos
STATIC_ANALYSIS_CONFIG={project_root}/static_analysis_config.yml
PROJECT_ROOT={project_root}
DIAGRAM_DEPTH_LEVEL=1
CACHING_DOCUMENTATION=false

# Monitoring Configuration
ENABLE_MONITORING=false

# ============================================================================
# LLM PROVIDER OPTIONS (uncomment and configure as needed)
# ============================================================================

# OpenAI Configuration
# OPENAI_API_KEY=your_openai_api_key_here
# OPENAI_BASE_URL=https://api.openai.com/v1  # Optional: Custom OpenAI endpoint

# Anthropic Configuration
# ANTHROPIC_API_KEY=your_anthropic_api_key_here

# Google AI Configuration
# GOOGLE_API_KEY=your_google_api_key_here

# Vercel Configuration
# VERCEL_API_KEY=your_vercel_api_key_here
# VERCEL_BASE_URL=https://gateway.ai.vercel.com/v1/projects/your_project_id/gateways/your_gateway_id # Optional: Custom Vercel endpoint

# AWS Bedrock Configuration
# AWS_BEARER_TOKEN_BEDROCK=your_aws_bearer_token_here

# Cerebras Configuration
# CEREBRAS_API_KEY=your_cerebras_api_key_here

# AGENT_MODEL=your_preferred_agent_model_here # Specify model to use for the main agent (e.g. gemini-3.0-flash)
# PARSING_MODEL=your_preferred_parsing_model_here # Optional: Specify model to use for parsing the output of the main agent (e.g. gemini-2.0-flash-lite)

# ============================================================================
# OPTIONAL SERVICES
# ============================================================================

# GitHub Integration
# GITHUB_TOKEN=your_github_token_here  # For accessing private repositories

# LangSmith Tracing (Optional)
# LANGSMITH_TRACING=false
# LANGSMITH_ENDPOINT=https://api.smith.langchain.com
# LANGSMITH_PROJECT=your_project_name
# LANGCHAIN_API_KEY=your_langchain_api_key_here

# ============================================================================
# NOTES
# ============================================================================
#
# Tip: Our experience has shown that using Google Gemini-2.5-Pro yields
#         the best results for complex diagram generation tasks.
#
# Configuration: After setup, verify paths in static_analysis_config.yml
#                   point to the correct executables for your system.
#
# Documentation: Visit https://codeboarding.org for more information
#
"""

    # Write the .env file
    try:
        with open(env_file_path, "w") as f:
            f.write(env_content)

        print("Step: .env file creation finished: success")

    except Exception as e:
        print(f"Step: .env file creation finished: failure - {e}")


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


def print_language_support_summary(npm_available: bool):
    """Print which language analyses are currently available based on installed tools."""
    print("Step: Language support summary")

    servers_dir = Path("static_analyzer/servers").resolve()
    platform_bin_dir = get_platform_bin_dir(servers_dir)
    is_win = platform.system() == "Windows"
    node_ext = ".cmd" if is_win else ""

    ts_path = servers_dir / "node_modules" / ".bin" / f"typescript-language-server{node_ext}"
    php_path = servers_dir / "node_modules" / ".bin" / f"intelephense{node_ext}"
    py_node_path = servers_dir / "node_modules" / ".bin" / f"pyright-langserver{node_ext}"
    py_env_path = shutil.which("pyright-langserver") or shutil.which("pyright-python-langserver")
    go_path = platform_bin_dir / ("gopls.exe" if is_win else "gopls")
    java_path = servers_dir / "bin" / "jdtls"

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


if __name__ == "__main__":
    # Windows consoles default to cp1252 which can't encode emojis; force UTF-8.
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

    args = parse_args()

    print("🚀 CodeBoarding Installation Script")
    print("=" * 40)

    # Step 1: Validate uv environment
    check_uv_environment()

    # Step 2: Check for npm and install Node.js based servers if available
    npm_available = resolve_npm_availability(auto_install_npm=args.auto_install_npm)
    if npm_available:
        install_node_servers()

    # Step 3: Download binaries from GitHub release
    download_binaries(auto_install_vcpp=args.auto_install_vcpp)

    # Step 4: Download JDTLS from GitHub release
    download_jdtls()

    # Step 5: Update configuration file with absolute paths
    update_static_analysis_config()

    # Step 6: Initialize .env file
    init_dot_env_file()

    # Step 6: Install pre-commit hooks
    install_pre_commit_hooks()

    # Step 7: Print language analysis availability
    print_language_support_summary(npm_available)

    print("\n" + "=" * 40)
    print("🎉 Installation completed!")

    print("📝 Don't forget to configure your .env file with your preferred LLM provider!")
    print("All set! You can run: python main.py <github_repo_url> --output-dir <output_path>")
