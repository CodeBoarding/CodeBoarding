"""TypeScript and JavaScript language adapter using typescript-language-server."""

from __future__ import annotations

from pathlib import Path

from static_analyzer.config import Language, file_extensions_for
from static_analyzer.engine.language_adapter import LanguageAdapter


class TypeScriptAdapter(LanguageAdapter):

    @property
    def file_extensions(self) -> tuple[str, ...]:
        """Both families: one tsserver serves them, and ``allowJs`` puts .js in a TS project.

        Why not just the TypeScript set: this adapter owns the whole family, so a narrower
        tuple makes the incremental pass invalidate a changed ``.js`` and then filter it back
        out — its nodes are dropped and never rebuilt.
        """
        return file_extensions_for(Language.TYPESCRIPT)

    @property
    def language(self) -> str:
        return "TypeScript"

    @property
    def language_enum(self) -> Language:
        return Language.TYPESCRIPT

    @property
    def lsp_command(self) -> list[str]:
        return ["typescript-language-server", "--stdio"]

    @property
    def language_id(self) -> str:
        return "typescript"

    def extract_package(self, qualified_name: str) -> str:
        return self._extract_deep_package(qualified_name)

    def get_all_packages(self, source_files: list[Path], project_root: Path) -> set[str]:
        return self._get_hierarchical_packages(source_files, project_root)


class JavaScriptAdapter(TypeScriptAdapter):

    @property
    def language(self) -> str:
        return "JavaScript"

    @property
    def language_enum(self) -> Language:
        return Language.JAVASCRIPT

    @property
    def language_id(self) -> str:
        return "javascript"

    @property
    def config_key(self) -> str:
        return "typescript"
