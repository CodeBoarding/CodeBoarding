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
    if not hasattr(sys, 'base_prefix') or sys.base_prefix == sys.prefix:
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
        with open(uv_marker, 'r') as f:
            content = f.read()
            if 'uv' not in content.lower():
                print("Step: Environment validation finished: warning - May not be uv environment")

    print("Step: Environment validation finished: success")


def check_npm():
    """Check if npm is installed on the system."""
    print("Step: npm check started")

    npm_path = shutil.which('npm')

    if npm_path:
        try:
            result = subprocess.run(['npm', '--version'], capture_output=True, text=True, check=True)
            print(f"Step: npm check finished: success (version {result.stdout.strip()})")
            return True
        except Exception:
            print("Step: npm check finished: failure - npm command failed. Skipping TypeScript Language Server installation.")
            return False
    else:
        print("Step: npm check finished: failure - npm not found")
        print("   Install Node.js from: https://nodejs.org/")
        return False


def install_typescript_language_server():
    """Install TypeScript Language Server using npm in the servers directory."""
    print("Step: TypeScript Language Server installation started")

    servers_dir = Path("static_analyzer/servers")
    servers_dir.mkdir(parents=True, exist_ok=True)

    original_cwd = os.getcwd()
    try:
        # Change to the servers directory
        os.chdir(servers_dir)

        # Initialize package.json if it doesn't exist
        if not Path("package.json").exists():
            subprocess.run(['npm', 'init', '-y'], check=True, capture_output=True, text=True)

        # Install typescript-language-server and typescript
        subprocess.run(['npm', 'install', 'typescript-language-server', 'typescript'], check=True, capture_output=True,
                       text=True)

        # Verify the installation
        ts_lsp_path = Path("./node_modules/.bin/typescript-language-server")
        if ts_lsp_path.exists():
            print("Step: TypeScript Language Server installation finished: success")
        else:
            print("Step: TypeScript Language Server installation finished: warning - Binary not found")

        return True

    except subprocess.CalledProcessError as e:
        print(f"Step: TypeScript Language Server installation finished: failure - {e}")
        return False
    except Exception as e:
        print(f"Step: TypeScript Language Server installation finished: failure - {e}")
        return False
    finally:
        # Always return to original directory
        os.chdir(original_cwd)


def download_file_from_gdrive(file_id, destination):
    """Download a file from Google Drive with proper handling of large files."""
    import requests

    # First try direct download
    url = f"https://drive.google.com/uc?export=download&id={file_id}"

    session = requests.Session()
    response = session.get(url, stream=True)

    # Check if we need to handle the download confirmation
    token = None
    for key, value in response.cookies.items():
        if key.startswith('download_warning'):
            token = value
            break

    if token:
        # Handle large file download confirmation
        params = {'id': file_id, 'confirm': token}
        response = session.get(url, params=params, stream=True)

    # Save the file
    with open(destination, 'wb') as f:
        for chunk in response.iter_content(chunk_size=32768):
            if chunk:
                f.write(chunk)

    return response.status_code == 200


def download_binary_from_gdrive():
    """Download binaries from Google Drive."""
    print("Step: Binary download started")

    # File IDs extracted from your share links
    mac_files = {
        "py-lsp": "1a8FaSGq27dyrN5yrKKMOWqfm3H8BK9Zf",
        "tokei": "1IKJSB7DHXAFZZQfwGOt6LypVUDlCQTLc",
        "gopls": "1gROk7g88qNDg7eGWqtzOVqitktUXA65c"
    }
    win_files = {
        "py-lsp": "1XKRsteNhUpu2eGhkYqRhXDvJYGhBpV01",
        "tokei": "15dKUK0bSZ1dUexbJpnx5WSv_Lqj1kyWK",
        "gopls": "162AdxaSb58IPNv_vvqTWUTtZJIo8Xrf_"
    }
    linux_files = {
        "py-lsp": "17XcohKWZKHv26DgRIdrxcPRMN0LKyt0i",
        "tokei": "1Wbx3bK0j-5c-hTJCfPcd86jqfQY0JsvF",
        "gopls": "1MYlJiT2fOb9aIQnlB7jRCE6cxQ5_71U2",
    }

    system = platform.system()
    if system == "Darwin":
        file_ids = mac_files
    elif system == "Windows":
        file_ids = win_files
    elif system == "Linux":
        file_ids = linux_files
    else:
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
                if platform.system() != 'Windows':
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
    with open(config_path, 'r') as f:
        config = yaml.safe_load(f)

    # Get the absolute path to the project root
    project_root = Path.cwd().resolve()
    servers_dir = project_root / "static_analyzer" / "servers"

    updates = 0

    # Update Python LSP server path
    py_lsp_path = servers_dir / "py-lsp"
    if py_lsp_path.exists():
        config['lsp_servers']['python']['command'][0] = str(py_lsp_path)
        updates += 1

    # Update TypeScript Language Server path
    ts_lsp_path = servers_dir / "node_modules" / ".bin" / "typescript-language-server"
    if ts_lsp_path.exists():
        config['lsp_servers']['typescript']['command'][0] = str(ts_lsp_path)
        updates += 1

    # Update tokei tool path
    tokei_path = servers_dir / "tokei"
    if tokei_path.exists():
        config['tools']['tokei']['command'][0] = str(tokei_path)
        updates += 1

    # Write the updated configuration back to file
    with open(config_path, 'w') as f:
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
ROOT_RESULT={project_root}/results
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

# AWS Bedrock Configuration
# AWS_BEARER_TOKEN_BEDROCK=your_aws_bearer_token_here

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
        with open(env_file_path, 'w') as f:
            f.write(env_content)

        print("Step: .env file creation finished: success")

    except Exception as e:
        print(f"Step: .env file creation finished: failure - {e}")


if __name__ == "__main__":
    print("🚀 CodeBoarding Installation Script")
    print("=" * 40)

    # Step 1: Validate uv environment
    check_uv_environment()

    # Step 2: Check for npm and install TypeScript Language Server if available
    npm_available = check_npm()
    if npm_available:
        install_typescript_language_server()

    # Step 3: Download binary from Google Drive (fallback if npm installation failed)
    download_binary_from_gdrive()

    # Step 4: Update configuration file with absolute paths
    update_static_analysis_config()

    # Step 5: Initialize .env file
    init_dot_env_file()

    print("\n" + "=" * 40)
    print("🎉 Installation completed!")

    print("📝 Don't forget to configure your .env file with your preferred LLM provider!")
    print("All set you can run: python demo.py <github_repo_url> --output-dir <output_path>")
