VSCODE_CONFIG = {
    "lsp_servers": {
        "python": {
            "name": "Pyright Language Server",
            "command": ["pyright-langserver", "--stdio"],
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
        "github_linguist": {
            "name": "GitHub Linguist",
            "command": ["github-linguist", "-b", "-j"],
            "description": "Analyze repository languages and file types",
            "install_command": "gem install github-linguist",
            "output_format": "json"
        }
    }
}
