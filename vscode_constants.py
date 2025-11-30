import os
import platform
from typing import List


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
    is_windows = platform.system().lower() == "windows"

    # Languages that need 'node' prefix on Windows
    node_languages = {"typescript", "python", "php"}

    for section in VSCODE_CONFIG.values():
        for key, value in section.items():
            cmd: list[str] = value["command"]  # type: ignore[assignment]
            if key == "typescript":
                # Scan the bin dir to find the cli.mjs path
                cmd[0] = (
                    find_runnable(bin_dir, "cli.mjs", "typescript-language-server")
                    or find_runnable(bin_dir, "typescript-language-server", "node_modules")
                    or cmd[0]
                )
            elif key == "python":
                cmd[0] = (
                    find_runnable(bin_dir, "langserver.index.js", "pyright")
                    or find_runnable(bin_dir, "pyright", "node_modules")
                    or cmd[0]
                )
            elif key == "php":
                cmd[0] = (
                    find_runnable(bin_dir, "intelephense.js", "intelephense")
                    or find_runnable(bin_dir, "intelephense", "node_modules")
                    or cmd[0]
                )
            elif "command" in value:
                if isinstance(cmd, list) and cmd:
                    cmd[0] = os.path.join(bin_path, cmd[0])

            # Apply Windows-specific node prefix for specified languages
            if is_windows and key in node_languages:
                cmd.insert(0, "node")


def find_runnable(bin_dir, search_file, part_of_dir):
    for root, dirs, files in os.walk(bin_dir):
        if search_file in files and part_of_dir in root:
            return os.path.join(root, search_file)
    return None


def update_config(bin_dir=None):
    if bin_dir:
        update_command_paths(bin_dir)


VSCODE_CONFIG = {
    "lsp_servers": {
        "python": {
            "name": "Pyright Language Server",
            "command": ["pyright-langserver", "--stdio"],
            "languages": ["python"],
            "file_extensions": [".py", ".pyi"],
            "install_commands": "npm install pyright",
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
        "php": {
            "name": "Intelephense",
            "command": ["intelephense", "--stdio"],
            "languages": ["php"],
            "file_extensions": [".php"],
            "install_commands": "npm install intelephense",
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
