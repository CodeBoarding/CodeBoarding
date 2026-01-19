import os
import platform
import shutil
import subprocess
import sys
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


def download_file_from_gdrive(file_id, destination):
    """Download a file from Google Drive with proper handling of large files."""
    import requests

    session = requests.Session()

    # Try the new download URL format with confirmation
    url = "https://drive.usercontent.google.com/download"
    params = {"id": file_id, "export": "download", "confirm": "t"}

    response = session.get(url, params=params, stream=True)

    # If that didn't work, try the old method
    if response.status_code != 200:
        url = f"https://drive.google.com/uc?export=download&id={file_id}"
        response = session.get(url, stream=True)

        # Check if we need to handle the download confirmation
        token = None
        for key, value in response.cookies.items():
            if key.startswith("download_warning"):
                token = value
                break

        if token:
            # Handle large file download confirmation
            params = {"id": file_id, "confirm": token}
            response = session.get(url, params=params, stream=True)

    # Save the file
    with open(destination, "wb") as f:
        for chunk in response.iter_content(chunk_size=32768):
            if chunk:
                f.write(chunk)

    return response.status_code == 200


def download_jdtls():
    """Download and extract JDTLS from Eclipse."""
    print("Step: JDTLS download started")

    import requests
    import tarfile

    servers_dir = Path("static_analyzer/servers")
    jdtls_dir = servers_dir / "jdtls"

    # Use a stable milestone version of JDTLS
    # This URL points to a recent stable release
    jdtls_url = "https://download.eclipse.org/jdtls/milestones/1.54.0/jdt-language-server-1.54.0-202511261751.tar.gz"
    jdtls_archive = servers_dir / "jdtls.tar.gz"

    try:
        # Download JDTLS if not already present
        if not jdtls_dir.exists():
            print("  Downloading JDTLS from Eclipse...")
            response = requests.get(jdtls_url, stream=True, timeout=300)
            response.raise_for_status()

            with open(jdtls_archive, "wb") as f:
                for chunk in response.iter_content(chunk_size=32768):
                    if chunk:
                        f.write(chunk)

            print("  Extracting JDTLS...")
            jdtls_dir.mkdir(parents=True, exist_ok=True)

            with tarfile.open(jdtls_archive, "r:gz") as tar:
                tar.extractall(path=jdtls_dir)

            # Clean up archive
            jdtls_archive.unlink()

            print("Step: JDTLS download finished: success")
        else:
            print("Step: JDTLS download finished: already exists")

        return True

    except Exception as e:
        print(f"Step: JDTLS download finished: failure - {e}")
        print("  You can manually download JDTLS from:")
        print("  https://download.eclipse.org/jdtls/milestones/")
        print("  and extract to static_analyzer/servers/jdtls/")
        return False


def download_binary_from_gdrive():
    """Download binaries from Google Drive."""
    print("Step: Binary download started")

    # File IDs extracted from your share links
    mac_files = {
        "tokei": "1IKJSB7DHXAFZZQfwGOt6LypVUDlCQTLc",
        "gopls": "1gROk7g88qNDg7eGWqtzOVqitktUXA65c",
    }
    win_files = {
        "tokei": "15dKUK0bSZ1dUexbJpnx5WSv_Lqj1kyWK",
        "gopls": "162AdxaSb58IPNv_vvqTWUTtZJIo8Xrf_",
    }
    linux_files = {
        "tokei": "1Wbx3bK0j-5c-hTJCfPcd86jqfQY0JsvF",
        "gopls": "1MYlJiT2fOb9aIQnlB7jRCE6cxQ5_71U2",
    }

    system = platform.system()
    match system:
        case "Darwin":
            file_ids = mac_files
        case "Windows":
            file_ids = win_files
        case "Linux":
            file_ids = linux_files
        case _:
            print(f"Step: Binary download finished: failure - Unsupported OS: {system}")
            return

    # Create servers directory
    servers_dir = Path("static_analyzer/servers")
    servers_dir.mkdir(parents=True, exist_ok=True)

    # Download each binary
    success_count = 0
    for binary_name, file_id in file_ids.items():
        binary_path = servers_dir / binary_name

        try:
            # Remove existing file if it exists
            if binary_path.exists():
                binary_path.unlink()

            # Download the file
            success = download_file_from_gdrive(file_id, binary_path)

            if success and binary_path.exists():
                # Make the binary executable on Unix-like systems
                if platform.system() != "Windows":
                    os.chmod(binary_path, 0o755)

                # Verify the file is not empty
                if binary_path.stat().st_size > 0:
                    success_count += 1
                else:
                    binary_path.unlink()  # Remove empty file

        except Exception as e:
            pass  # Continue with other downloads

    if success_count == len(file_ids):
        print("Step: Binary download finished: success")
    elif success_count > 0:
        print(f"Step: Binary download finished: partial success ({success_count}/{len(file_ids)} binaries)")
    else:
        print("Step: Binary download finished: failure - No binaries downloaded")


def update_static_analysis_config():
    """Update static_analysis_config.yml with correct paths to binaries."""
    print("Step: Configuration update started")

    import yaml

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

    # Update JDTLS configuration
    jdtls_dir = servers_dir / "jdtls"
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

# Hugging Face Hub (for evaluation datasets)
# HF_TOKEN=your_huggingface_token_here  # Get token from https://huggingface.co/settings/tokens

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


if __name__ == "__main__":
    print("🚀 CodeBoarding Installation Script")
    print("=" * 40)

    # Step 1: Validate uv environment
    check_uv_environment()

    # Step 2: Check for npm and install Node.js based servers if available
    npm_available = check_npm()
    if npm_available:
        install_node_servers()

    # Step 3: Download binary from Google Drive
    download_binary_from_gdrive()

    # Step 4: Download JDTLS for Java support
    download_jdtls()

    # Step 5: Update configuration file with absolute paths
    update_static_analysis_config()

    # Step 6: Initialize .env file
    init_dot_env_file()

    # Step 6: Install pre-commit hooks
    install_pre_commit_hooks()

    print("\n" + "=" * 40)
    print("🎉 Installation completed!")

    print("📝 Don't forget to configure your .env file with your preferred LLM provider!")
    print("All set you can run: python demo.py <github_repo_url> --output-dir <output_path>")
