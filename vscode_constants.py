import logging
import os
import platform

logger = logging.getLogger(__name__)


def get_bin_path(bin_dir):
    system = platform.system().lower()
    subdirs = {"windows": "win", "darwin": "macos", "linux": "linux"}
    if system not in subdirs:
        raise RuntimeError(
            f"Unsupported platform: {system}. The extension currently supports Windows, macOS, and Linux."
        )
    return os.path.join(bin_dir, "bin", subdirs[system])


def _runnable_native(path: str) -> bool:
    """True when a native binary exists and is executable, restoring a lost exec bit when possible.

    Why: a mode-dropping unpack/copy leaves the binary at 0644; wiring its absolute
    path into the command guarantees EACCES at Popen and disables the PATH fallback
    in tool_registry.build_config. Mirrors the repair in tool_registry.installers
    (not imported from there: tool_registry.manifest imports this module).
    """
    if platform.system().lower() == "windows":
        # No exec bit on Windows; Popen resolves the suffix-less path to .exe itself.
        return os.path.exists(path) or os.path.exists(path + ".exe")
    if not os.path.exists(path):
        return False
    if os.access(path, os.X_OK):
        return True
    try:
        os.chmod(path, 0o755)
        logger.info("Restored exec bit on %s", path)
        return True
    except OSError:
        logger.warning("%s exists but is not executable and chmod failed; check ownership/mount", path)
        return False


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
            elif key == "java":
                # Find JDTLS root directory
                jdtls_dir = os.path.join(bin_dir, "bin", "jdtls")
                if os.path.exists(jdtls_dir):
                    # Store the JDTLS root path for JavaClient to use
                    # The actual command will be constructed dynamically in JavaClient
                    value["jdtls_root"] = jdtls_dir
                    # Keep command as "java" - it will be constructed by JavaClient
                    cmd[0] = "java"
            elif "command" in value:
                if isinstance(cmd, list) and cmd:
                    candidate = os.path.join(bin_path, cmd[0])
                    # Keep the bare name when the binary is unusable so the PATH
                    # fallback in tool_registry.build_config stays available.
                    if _runnable_native(candidate):
                        cmd[0] = candidate

            # Apply Windows-specific node prefix for specified languages
            # Use VSCode's bundled Node.js (passed via CODEBOARDING_NODE_PATH) so users
            # don't need a separate Node.js/npm installation on Windows.
            if is_windows and key in node_languages:
                node_path = os.environ.get("CODEBOARDING_NODE_PATH", "node")
                cmd.insert(0, node_path)


def find_runnable(bin_dir, search_file, part_of_dir):
    for root, _, files in os.walk(bin_dir):
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
        "csharp": {
            "name": "csharp-ls Language Server",
            "command": ["csharp-ls"],
            "languages": ["csharp"],
            "file_extensions": [".cs"],
            # csharp-ls is installed via `dotnet tool install --tool-path` by
            # tool_registry. Full migration mode targets .NET 10.
            "install_commands": "codeboarding-setup (installs csharp-ls automatically; requires .NET SDK 10.0+)",
        },
        "java": {
            "name": "Eclipse JDT Language Server",
            "command": ["java"],  # Placeholder - will be resolved by update_command_paths()
            "languages": ["java"],
            "file_extensions": [".java"],
            "install_commands": "null",
        },
        "rust": {
            "name": "rust-analyzer",
            "command": ["rust-analyzer"],
            "languages": ["rust"],
            "file_extensions": [".rs"],
            # rust-analyzer is downloaded from rust-lang/rust-analyzer releases
            # by tool_registry; the install_commands string is informational only
            # and surfaces in error messages when the binary cannot be located.
            "install_commands": "codeboarding-setup (downloads rust-analyzer automatically)",
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
