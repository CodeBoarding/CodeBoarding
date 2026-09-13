"""TypeScript and JavaScript language adapter using typescript-language-server."""

from __future__ import annotations

from pathlib import Path

from repo_utils.ignore import RepoIgnoreManager
from static_analyzer.config import Language
from static_analyzer.engine.language_adapter import LanguageAdapter


class TypeScriptAdapter(LanguageAdapter):

    @property
    def results_language(self) -> Language:
        return Language.TYPESCRIPT

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

    @property
    def resolves_method_groups(self) -> bool:
        return True

    def get_lsp_init_options(self, ignore_manager: RepoIgnoreManager | None = None) -> dict:
        # Why: by default a syntax-only server answers while the project still loads, and a definition
        # that needs an inferred type -- ``.then(t => t.serialize())`` -- comes back empty on a cold start.
        return {"tsserver": {"useSyntaxServer": "never"}}

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
