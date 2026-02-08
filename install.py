import os
import platform
import shutil
import subprocess
import sys
import requests
import tarfile
import yaml
from pathlib import Path


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
        print("   Install Node.js from: https://nodejs.org/")
        return False


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


def download_binaries():
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

    try:
        tag = get_latest_release_tag()
        print(f"  Using release: {tag}")
    except Exception as e:
        print(f"Step: Binary download finished: failure - Could not determine latest release: {e}")
        return

    success_count = 0
    for local_name, asset_name in binaries.items():
        ext = ".exe" if system == "Windows" else ""
        binary_path = servers_dir / (local_name + ext)

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
            tar.extractall(path=jdtls_dir)

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

    updates = 0
    is_win = platform.system() == "Windows"

    # The Plan: (Binary Name, Is_Node_App, List of Config Targets)
    # "True" means it lives in node_modules/.bin and needs .cmd on Windows
    # "False" means it lives in the root and needs .exe on Windows
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
        folder = (servers_dir / "node_modules" / ".bin") if is_node else servers_dir

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
    is_win = platform.system() == "Windows"
    node_ext = ".cmd" if is_win else ""

    ts_path = servers_dir / "node_modules" / ".bin" / f"typescript-language-server{node_ext}"
    php_path = servers_dir / "node_modules" / ".bin" / f"intelephense{node_ext}"
    py_node_path = servers_dir / "node_modules" / ".bin" / f"pyright-langserver{node_ext}"
    py_env_path = shutil.which("pyright-langserver") or shutil.which("pyright-python-langserver")
    go_path = servers_dir / ("gopls.exe" if is_win else "gopls")
    java_path = servers_dir / "bin" / "jdtls"

    python_ok = py_node_path.exists() or bool(py_env_path)
    typescript_ok = npm_available and ts_path.exists()
    javascript_ok = typescript_ok
    php_ok = npm_available and php_path.exists()
    go_ok = go_path.exists()
    java_ok = java_path.exists()

    print(f"  - Python: {'yes' if python_ok else 'no'}")
    if not python_ok:
        print("    reason: pyright-langserver not found in node_modules or active environment")

    print(f"  - TypeScript: {'yes' if typescript_ok else 'no'}")
    if not typescript_ok:
        if not npm_available:
            print("    reason: npm not available")
        else:
            print("    reason: typescript-language-server binary not found")

    print(f"  - JavaScript: {'yes' if javascript_ok else 'no'}")
    if not javascript_ok:
        if not npm_available:
            print("    reason: npm not available")
        else:
            print("    reason: typescript-language-server binary not found")

    print(f"  - PHP: {'yes' if php_ok else 'no'}")
    if not php_ok:
        if not npm_available:
            print("    reason: npm not available")
        else:
            print("    reason: intelephense binary not found")

    print(f"  - Go: {'yes' if go_ok else 'no'}")
    if not go_ok:
        print("    reason: gopls binary not found")

    print(f"  - Java: {'yes' if java_ok else 'no'}")
    if not java_ok:
        print("    reason: jdtls installation not found")


if __name__ == "__main__":
    print("🚀 CodeBoarding Installation Script")
    print("=" * 40)

    # Step 1: Validate uv environment
    check_uv_environment()

    # Step 2: Check for npm and install Node.js based servers if available
    npm_available = check_npm()
    if npm_available:
        install_node_servers()

    # Step 3: Download binaries from GitHub release
    download_binaries()

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
