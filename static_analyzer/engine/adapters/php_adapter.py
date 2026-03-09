"""PHP language adapter using Intelephense."""

from __future__ import annotations

from pathlib import Path

from static_analyzer.engine.language_adapter import SYMBOL_KIND_MODULE, LanguageAdapter


class PHPAdapter(LanguageAdapter):

    @property
    def language(self) -> str:
        return "PHP"

    @property
    def file_extensions(self) -> tuple[str, ...]:
        return (".php",)

    @property
    def lsp_command(self) -> list[str]:
        return ["intelephense", "--stdio"]

    @property
    def language_id(self) -> str:
        return "php"

    def extract_package(self, qualified_name: str) -> str:
        return self._extract_deep_package(qualified_name)

    def get_lsp_init_options(self) -> dict:
        return {"clearCache": False}

    def is_reference_worthy(self, symbol_kind: int) -> bool:
        return super().is_reference_worthy(symbol_kind) or symbol_kind == SYMBOL_KIND_MODULE

    def get_excluded_dirs(self) -> set[str]:
        return super().get_excluded_dirs() | {
            "vendor",
            "cache",
            ".phpunit.cache",
        }

    def get_all_packages(self, source_files: list[Path], project_root: Path) -> set[str]:
        return self._get_hierarchical_packages(source_files, project_root)
