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
import tarfile
import yaml


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


def get_platform_bin_subdir() -> str:
    """Return OS-specific binary folder name used by the extension."""
    subdirs = {"windows": "win", "darwin": "macos", "linux": "linux"}
    system = platform.system().lower()
    if system not in subdirs:
        raise RuntimeError(f"Unsupported platform: {system}")
    return subdirs[system]


def get_platform_bin_dir(servers_dir: Path) -> Path:
    """Return static_analyzer/servers/bin/<os> directory."""
    return servers_dir / "bin" / get_platform_bin_subdir()


def install_node_servers():
    """Install Node.js based servers (TypeScript, Pyright) using npm in the servers directory."""
    print("Step: Node.js servers installation started")

    servers_dir = Path("static_analyzer/servers")
    servers_dir.mkdir(parents=True, exist_ok=True)

    original_cwd = os.getcwd()
    try:
        # Change to the servers directory
        os.chdir(servers_dir)

        npm_path = shutil.which("npm")

        if npm_path:
            # Initialize package.json if it doesn't exist
            if not Path("package.json").exists():
                subprocess.run([npm_path, "init", "-y"], check=True, capture_output=True, text=True)

            # Install typescript-language-server, typescript, pyright, and intelephense
            subprocess.run(
                [npm_path, "install", "typescript-language-server", "typescript", "pyright", "intelephense"],
                check=True,
                capture_output=True,
                text=True,
            )

        # Verify the installation
        ts_lsp_path = Path("./node_modules/.bin/typescript-language-server")
        py_lsp_path = Path("./node_modules/.bin/pyright-langserver")
        php_lsp_path = Path("./node_modules/.bin/intelephense")

        success = True
        if ts_lsp_path.exists():
            print("Step: TypeScript Language Server installation finished: success")
        else:
            print("Step: TypeScript Language Server installation finished: warning - Binary not found")
            success = False

        if py_lsp_path.exists():
            print("Step: Pyright Language Server installation finished: success")
        else:
            print("Step: Pyright Language Server installation finished: warning - Binary not found")
            success = False

        if php_lsp_path.exists():
            print("Step: Intelephense installation finished: success")
        else:
            print("Step: Intelephense installation finished: warning - Binary not found")
            success = False

        return success

    except subprocess.CalledProcessError as e:
        print(f"Step: Node.js servers installation finished: failure - {e}")
        return False
    except Exception as e:
        print(f"Step: Node.js servers installation finished: failure - {e}")
        return False
    finally:
        # Always return to original directory
        os.chdir(original_cwd)


VCREDIST_URL = "https://aka.ms/vs/17/release/vc_redist.x64.exe"
STATUS_DLL_NOT_FOUND = 0xC0000135  # 3221225781 unsigned
GITHUB_REPO = "CodeBoarding/CodeBoarding"


def get_latest_release_tag() -> str:
    """Get the latest release tag from GitHub using gh CLI or the API."""
    response = requests.get(f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest", timeout=30)
    response.raise_for_status()
    return response.json()["tag_name"]


def download_github_release_asset(tag: str, asset_name: str, destination: Path) -> bool:
    """Download a release asset from GitHub.

    Tries gh CLI first (handles authentication automatically), falls back to requests.

    Args:
        tag: The release tag (e.g., "v0.7.1")
        asset_name: Name of the asset file (e.g., "tokei-macos")
        destination: Local path to save the file

    Returns:
        True if download was successful
    """
    destination.parent.mkdir(parents=True, exist_ok=True)

    url = f"https://github.com/{GITHUB_REPO}/releases/download/{tag}/{asset_name}"
    response = requests.get(url, stream=True, timeout=300, allow_redirects=True)
    response.raise_for_status()

    with open(destination, "wb") as f:
        for chunk in response.iter_content(chunk_size=32768):
            if chunk:
                f.write(chunk)

    return destination.exists() and destination.stat().st_size > 0


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

    system = platform.system()
    platform_suffix_map = {
        "Darwin": "macos",
        "Windows": "windows.exe",
        "Linux": "linux",
    }

    if system not in platform_suffix_map:
        print(f"Step: Binary download finished: failure - Unsupported OS: {system}")
        return

    suffix = platform_suffix_map[system]
    binaries = {
        "tokei": f"tokei-{suffix}",
        "gopls": f"gopls-{suffix}",
    }

    servers_dir = Path("static_analyzer/servers")
    servers_dir.mkdir(parents=True, exist_ok=True)
    platform_bin_dir = get_platform_bin_dir(servers_dir)
    platform_bin_dir.mkdir(parents=True, exist_ok=True)

    try:
        tag = get_latest_release_tag()
        print(f"  Using release: {tag}")
    except Exception as e:
        print(f"Step: Binary download finished: failure - Could not determine latest release: {e}")
        return

    success_count = 0
    for local_name, asset_name in binaries.items():
        ext = ".exe" if system == "Windows" else ""
        binary_path = platform_bin_dir / (local_name + ext)

        try:
            if binary_path.exists():
                binary_path.unlink()

            success = download_github_release_asset(tag, asset_name, binary_path)

            if success:
                if system != "Windows":
                    os.chmod(binary_path, 0o755)
                success_count += 1
                print(f"  {local_name}: downloaded successfully")
            else:
                print(f"  {local_name}: download failed (empty file)")
                binary_path.unlink(missing_ok=True)

        except Exception as e:
            print(f"  {local_name}: download failed - {e}")
            binary_path.unlink(missing_ok=True)

    if success_count == len(binaries):
        print("Step: Binary download finished: success")
    elif success_count > 0:
        print(f"Step: Binary download finished: partial success ({success_count}/{len(binaries)} binaries)")
    else:
        print("Step: Binary download finished: failure - No binaries downloaded")

    # Verify downloaded binaries actually work (catch missing DLL issues on Windows)
    if system == "Windows" and success_count > 0:
        vcpp_resolved = False
        needs_vcpp = False
        for local_name in binaries:
            binary_path = platform_bin_dir / f"{local_name}.exe"
            if not binary_path.exists():
                continue

            if not verify_binary(binary_path):
                print(f"  {local_name}: verification failed - missing Visual C++ runtime")
                needs_vcpp = True
            else:
                print(f"  {local_name}: verification passed")

        if needs_vcpp:
            vcpp_resolved = resolve_missing_vcpp(auto_install_vcpp=auto_install_vcpp)
            if vcpp_resolved:
                for local_name in binaries:
                    binary_path = platform_bin_dir / f"{local_name}.exe"
                    if binary_path.exists() and verify_binary(binary_path):
                        print(f"  {local_name}: verification passed after VC++ install")


def download_jdtls():
    """Download and extract JDTLS from the latest GitHub release."""
    print("Step: JDTLS download started")

    servers_dir = Path("static_analyzer/servers")
    jdtls_dir = servers_dir / "bin" / "jdtls"

    if jdtls_dir.exists() and (jdtls_dir / "plugins").exists():
        print("Step: JDTLS download finished: already exists")
        return True

    jdtls_dir.mkdir(parents=True, exist_ok=True)
    jdtls_archive = servers_dir / "bin" / "jdtls.tar.gz"

    try:
        tag = get_latest_release_tag()
        print(f"  Downloading JDTLS from GitHub release {tag}...")

        success = download_github_release_asset(tag, "jdtls.tar.gz", jdtls_archive)
        if not success:
            print("Step: JDTLS download finished: failure - Download returned empty file")
            return False

        print("  Extracting JDTLS...")
        with tarfile.open(jdtls_archive, "r:gz") as tar:
            tar.extractall(path=jdtls_dir, filter="tar")

        jdtls_archive.unlink()

        print("Step: JDTLS download finished: success")
        return True

    except Exception as e:
        print(f"Step: JDTLS download finished: failure - {e}")
        jdtls_archive.unlink(missing_ok=True)
        return False


def update_static_analysis_config():
    """Update static_analysis_config.yml with correct paths to binaries."""
    print("Step: Configuration update started")

    config_path = Path("static_analysis_config.yml")
    if not config_path.exists():
        print("Step: Configuration update finished: failure - static_analysis_config.yml not found")
        return

    # Read the current configuration
    with open(config_path, "r") as f:
        config = yaml.safe_load(f)

    # Get the absolute path to the project root
    project_root = Path.cwd().resolve()
    servers_dir = project_root / "static_analyzer" / "servers"
    platform_bin_dir = get_platform_bin_dir(servers_dir)

    updates = 0
    is_win = platform.system() == "Windows"

    # The Plan: (Binary Name, Is_Node_App, List of Config Targets)
    # "True" means it lives in node_modules/.bin and needs .cmd on Windows
    # "False" means it lives under bin/<os> and needs .exe on Windows
    server_definitions = [
        ("pyright-langserver", True, [("lsp_servers", "python")]),
        ("typescript-language-server", True, [("lsp_servers", "typescript"), ("lsp_servers", "javascript")]),
        ("intelephense", True, [("lsp_servers", "php")]),
        ("gopls", False, [("lsp_servers", "go")]),
        ("tokei", False, [("tools", "tokei")]),
        # Java is handled differently as it isn't an executable
    ]

    for binary, is_node, targets in server_definitions:
        # 1. Determine the extension and folder based on the type
        ext = (".cmd" if is_node else ".exe") if is_win else ""
        folder = (servers_dir / "node_modules" / ".bin") if is_node else platform_bin_dir

        # 2. Build the full path once
        full_path = folder / (binary + ext)

        # 3. Apply to all relevant targets in config
        if full_path.exists():
            for section, key in targets:
                # Handle language server key for Intelephense, which is "php" not "Intelephense Language Server"
                if binary == "intelephense":
                    key = "php"
                elif binary == "pyright-langserver":
                    key = "python"

                config[section][key]["command"][0] = str(full_path)
                updates += 1

    # Minimal fallback: if pyright wasn't installed under node_modules, use active env binary if available
    node_ext = ".cmd" if is_win else ""
    node_pyright = servers_dir / "node_modules" / ".bin" / f"pyright-langserver{node_ext}"
    if not node_pyright.exists():
        env_pyright = shutil.which("pyright-langserver") or shutil.which("pyright-python-langserver")
        if env_pyright and "lsp_servers" in config and "python" in config["lsp_servers"]:
            config["lsp_servers"]["python"]["command"][0] = env_pyright
            updates += 1

    # Update JDTLS configuration
    jdtls_dir = servers_dir / "bin" / "jdtls"
    if jdtls_dir.exists() and "lsp_servers" in config and "java" in config["lsp_servers"]:
        config["lsp_servers"]["java"]["jdtls_root"] = str(jdtls_dir)
        updates += 1

    # Write the updated configuration back to file
    with open(config_path, "w") as f:
        yaml.safe_dump(config, f, default_flow_style=False, sort_keys=False)

    print(f"Step: Configuration update finished: success ({updates} paths updated)")


def init_dot_env_file():
    """Initialize .env file with default configuration and commented examples."""
    print("Step: .env file creation started")

    env_file_path = Path(".env")

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
    print("All set you can run: python demo.py <github_repo_url> --output-dir <output_path>")
