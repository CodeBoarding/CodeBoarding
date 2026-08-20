"""TypeScript and JavaScript language adapter using typescript-language-server."""

from __future__ import annotations

from pathlib import Path

from static_analyzer.constants import Language
from static_analyzer.engine.language_adapter import LanguageAdapter


class TypeScriptAdapter(LanguageAdapter):

    _JSX_LANGUAGE_IDS = {".tsx": "typescriptreact", ".jsx": "javascriptreact"}

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

    def language_id_for(self, file_path: Path) -> str:
        """Send the JSX dialect for .tsx/.jsx so tsserver enables JSX parsing.

        Why: typescript-language-server maps languageId to tsserver's scriptKind, and a
        .tsx opened as plain "typescript" parses ``<div ...>`` as a type assertion — the
        component's symbols come back flattened, truncated and partly unnamed.
        """
        return self._JSX_LANGUAGE_IDS.get(file_path.suffix, self.language_id)

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
