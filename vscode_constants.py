import os
import platform


def get_bin_path(bin_dir):
    system = platform.system().lower()
    subdirs = {"windows": "win", "darwin": "macos", "linux": "linux"}
    if system not in subdirs:
        raise RuntimeError(
            f"Unsupported platform: {system}. The extension currently supports Windows, macOS, and Linux."
        )
    return os.path.join(bin_dir, "bin", subdirs[system])


def update_command_paths(bin_dir):
    bin_path = get_bin_path(bin_dir)
    for section in VSCODE_CONFIG.values():
        for key, value in section.items():
            if key == "typescript":
                # Scan the bin dir to fine the cli.mjs path
                value["command"][0] = find_cli_js(bin_dir) or value["command"][0]
                if platform.system().lower() == "windows":
                    # Use node to run the .mjs file on Windows
                    value["command"].insert(0, "node")
            elif "command" in value:
                cmd = value["command"]
                if isinstance(cmd, list) and cmd:
                    value["command"][0] = os.path.join(bin_path, cmd[0])


def find_cli_js(bin_dir, search_file="cli.mjs"):
    for root, dirs, files in os.walk(bin_dir):
        if search_file in files and "typescript-language-server" in root:
            return os.path.join(root, search_file)
    return None


def update_config(bin_dir=None):
    if bin_dir:
        update_command_paths(bin_dir)


VSCODE_CONFIG = {
    "lsp_servers": {
        "python": {
            "name": "Pyright Language Server",
            "command": ["py-lsp", "--stdio"],
            "languages": ["python"],
            "file_extensions": [".py", ".pyi"],
            "install_commands": "pip install pyright",
        },
        "typescript": {
            "name": "TypeScript Language Server",
            "command": ["cli.mjs", "--stdio", "--log-level=2"],
            "languages": ["typescript", "javascript"],
            "file_extensions": [".ts", ".tsx", ".js", ".jsx"],
            "install_commands": "npm install --save-dev typescript-language-server typescript",
        },
        "go": {
            "name": "Go Language Server (gopls)",
            "command": ["gopls", "serve"],
            "languages": ["go"],
            "file_extensions": [".go"],
            "install_commands": "go install golang.org/x/tools/gopls@latest",
        },
    },
    "tools": {
        "tokei": {
            "name": "tokei",
            "command": ["tokei", "-o", "json"],
            "description": "Analyze repository languages and file types",
            "install_command": "conda install -c conda-forge tokei",
            "output_format": "json",
        }
    },
}
