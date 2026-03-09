"""Python language adapter using Pyright."""

from __future__ import annotations

from pathlib import Path

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

    def get_all_packages(self, source_files: list[Path], project_root: Path) -> set[str]:
        packages: set[str] = set()
        for f in source_files:
            rel = f.relative_to(project_root)
            parent_parts = rel.parent.parts
            if parent_parts and parent_parts[0] != ".":
                packages.add(".".join(parent_parts))
            else:
                packages.add(rel.stem)
        return packages

    def get_excluded_dirs(self) -> set[str]:
        return super().get_excluded_dirs() | {
            "__pycache__",
            ".venv",
            "venv",
            ".tox",
            ".mypy_cache",
            ".pytest_cache",
            "egg-info",
            ".eggs",
            "dist",
            "build",
        }
