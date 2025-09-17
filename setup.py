import os
import sys
import subprocess
import platform
import shutil
from pathlib import Path

# Try to import requests, install if not available
try:
    import requests
except ImportError:
    print("📦 Installing requests library for Google Drive downloads...")
    subprocess.run([sys.executable, '-m', 'pip', 'install', 'requests'], check=True)
    import requests


def check_uv_environment():
    """Validate that we're running within a uv virtual environment."""
    # Check if we're in a virtual environment
    if not hasattr(sys, 'base_prefix') or sys.base_prefix == sys.prefix:
        print("❌ Error: This script must be run within a virtual environment.")
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
                print("⚠️  Warning: Virtual environment may not be created by uv.")
    else:
        print("⚠️  Warning: Could not verify uv environment.")

    print("✅ Running in virtual environment:", sys.prefix)


def install_requirements():
    """Install Python requirements using uv pip."""
    requirements_file = 'requirements.txt'

    if not os.path.exists(requirements_file):
        print("❌ Error: No requirements file found.")
        sys.exit(1)

    print(f"📦 Installing requirements from {requirements_file}...")

    try:
        subprocess.run([
            'uv', 'pip', 'install', '-r', requirements_file
        ], check=True, capture_output=True, text=True)
        print("✅ Requirements installed successfully.")
    except subprocess.CalledProcessError as e:
        print(f"❌ Error installing requirements: {e}.\nUsed command: {' '.join(e.cmd)}")
        print(f"stdout: {e.stdout}")
        print(f"stderr: {e.stderr}")
        sys.exit(1)


def check_npm():
    """Check if npm is installed on the system."""
    npm_path = shutil.which('npm')

    if npm_path:
        try:
            result = subprocess.run(['npm', '--version'], capture_output=True, text=True, check=True)
            print(f"✅ npm found: version {result.stdout.strip()}")
            return True
        except subprocess.CalledProcessError:
            print("⚠️  Warning: npm command failed to execute.")
            return False
    else:
        print("⚠️  Warning: npm not found on system.")
        print("   TypeScript and JavaScript language support will not be available.")
        print("   To install npm, please install Node.js from: https://nodejs.org/")
        return False


def install_typescript_language_server():
    """Install TypeScript Language Server using npm in the servers directory."""
    servers_dir = Path("static_analyzer/servers")
    servers_dir.mkdir(parents=True, exist_ok=True)

    print("📦 Installing TypeScript Language Server...")
    original_cwd = os.getcwd()
    try:
        # Change to the servers directory
        os.chdir(servers_dir)

        # Initialize package.json if it doesn't exist
        if not Path("package.json").exists():
            print("📄 Creating package.json...")
            subprocess.run(['npm', 'init', '-y'], check=True, capture_output=True, text=True)

        # Install typescript-language-server and typescript
        print("📥 Installing typescript-language-server and typescript...")
        subprocess.run(['npm', 'install', 'typescript-language-server', 'typescript'], check=True, capture_output=True,
                       text=True)

        print("✅ TypeScript Language Server installed successfully.")

        # Verify the installation
        ts_lsp_path = Path("./node_modules/.bin/typescript-language-server")
        if ts_lsp_path.exists():
            print(f"✅ TypeScript Language Server binary found at: {ts_lsp_path}")
        else:
            print("⚠️  Warning: TypeScript Language Server binary not found in expected location.")

        return True

    except subprocess.CalledProcessError as e:
        print(f"❌ Error installing TypeScript Language Server: {e}")
        print(f"stdout: {e.stdout}")
        print(f"stderr: {e.stderr}")
        return False
    except Exception as e:
        print(f"❌ Unexpected error during npm installation: {e}")
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
    # File IDs extracted from your share links
    mac_files = {
        "py-lsp": "1a8FaSGq27dyrN5yrKKMOWqfm3H8BK9Zf",
        "tokei": "1IKJSB7DHXAFZZQfwGOt6LypVUDlCQTLc"
    }
    win_files = {
        "py-lsp": "1a8FaSGq27dyrN5yrKKMOWqfm3H8BK9Zf",
        "tokei": "1IKJSB7DHXAFZZQfwGOt6LypVUDlCQTLc"
    }
    linux_files = {
        "py-lsp": "17XcohKWZKHv26DgRIdrxcPRMN0LKyt0i",
        "tokei": "1Wbx3bK0j-5c-hTJCfPcd86jqfQY0JsvF"
    }

    system = platform.system()
    if system == "Darwin":
        file_ids = mac_files
    elif system == "Windows":
        file_ids = win_files
    elif system == "Linux":
        file_ids = linux_files
    else:
        print(f"❌ Unsupported OS: {system}. Cannot download binaries.")
        return

    # Create servers directory
    servers_dir = Path("static_analyzer/servers")
    servers_dir.mkdir(parents=True, exist_ok=True)

    # Download each binary
    for binary_name, file_id in file_ids.items():
        print(f"� Downloading {binary_name} binary...")
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
                    print(f"✅ {binary_name} binary downloaded successfully to: {binary_path}")
                else:
                    print(f"❌ {binary_name} binary downloaded but file is empty.")
                    binary_path.unlink()  # Remove empty file
            else:
                print(f"❌ Failed to download {binary_name} binary.")

        except Exception as e:
            print(f"❌ Error downloading {binary_name} binary: {e}")
            print(f"   Manual download links:")
            if binary_name == "py-lsp":
                print(f"   py-lsp: https://drive.google.com/file/d/{file_id}/view?usp=sharing")
            elif binary_name == "tokei":
                print(f"   tokei: https://drive.google.com/file/d/{file_id}/view?usp=sharing")


if __name__ == "__main__":
    print("🚀 CodeBoarding Installation Script")
    print("=" * 40)

    # Step 1: Validate uv environment
    check_uv_environment()

    # Step 2: Install Python requirements
    install_requirements()

    # Step 3: Check for npm and install TypeScript Language Server if available
    npm_available = check_npm()

    if npm_available:
        ts_lsp_installed = install_typescript_language_server()
    else:
        ts_lsp_installed = False

    # Step 4: Download binary from Google Drive (fallback if npm installation failed)
    download_binary_from_gdrive()

    print("\n" + "=" * 40)
    print("🎉 Installation completed!")

    if not npm_available:
        print("\n⚠️  Note: npm was not found. Consider installing Node.js for full language support.")
    elif ts_lsp_installed:
        print("\n✅ TypeScript Language Server installed via npm.")

    print("You can now run the CodeBoarding static analyzer.")
