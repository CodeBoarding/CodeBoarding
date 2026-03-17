"""Python language adapter using Pyright."""

from __future__ import annotations

from static_analyzer.engine.language_adapter import LanguageAdapter


class PythonAdapter(LanguageAdapter):

    @property
    def language(self) -> str:
        return "Python"

    @property
    def file_extensions(self) -> tuple[str, ...]:
        return (".py",)

    @property
    def lsp_command(self) -> list[str]:
        return ["pyright-langserver", "--stdio"]

    @property
    def language_id(self) -> str:
        return "python"

    def get_lsp_init_options(self) -> dict:
        return {
            "python": {
                "analysis": {
                    "autoSearchPaths": True,
                    "diagnosticMode": "workspace",
                    "useLibraryCodeForTypes": True,
                },
            },
        }
