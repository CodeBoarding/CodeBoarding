import os
import platform


def get_bin_path(bin_dir):
    system = platform.system().lower()
    subdirs = {
        'windows': 'win',
        'darwin': 'macos',
        'linux': 'linux'
    }
    if system not in subdirs:
        raise RuntimeError(
            f"Unsupported platform: {system}. The extension currently supports Windows, macOS, and Linux.")
    return os.path.join(bin_dir, 'bin', subdirs[system])


def update_command_paths(bin_dir):
    bin_path = get_bin_path(bin_dir)
    for section in VSCODE_CONFIG.values():
        for key, value in section.items():
            if key == 'typescript':
                value['command'][0] = os.path.join(bin_dir, 'node_modules',
                                                   '.bin', value['command'][0])
            elif "command" in value:
                cmd = value["command"]
                if isinstance(cmd, list) and cmd:
                    value["command"][0] = os.path.join(bin_path, cmd[0])


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
            "install_commands": "pip install pyright"
        },
        "typescript": {
            "name": "TypeScript Language Server",
            "command": ["typescript-language-server", "--stdio", "--log-level=2"],
            "languages": ["typescript", "javascript"],
            "file_extensions": [".ts", ".tsx", ".js", ".jsx"],
            "install_commands": "npm install --save-dev typescript-language-server typescript"
        }
    },
    "tools": {
        "tokei": {
            "name": "tokei",
            "command": ["tokei", "-o", "json"],
            "description": "Analyze repository languages and file types",
            "install_command": "conda install -c conda-forge tokei",
            "output_format": "json"
        }
    }
}
