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

    def get_lsp_init_options(self, ignore_manager: RepoIgnoreManager | None = None) -> dict:
        """Run one fully semantic tsserver, with no background typings install.

        Why: by default the language server also runs a syntax-only tsserver and routes
        references, definition, implementation and hover to it whenever it believes the
        semantic server is still loading a project. That belief starts out true and flips on
        asynchronous events, so a query sent early, or during a project reload, is answered
        without type information and silently drops call edges. Which queries land there
        depends on timing, so the graph differs run to run and platform to platform.
        Typings acquisition is off for the same reason: it installs ``@types`` from the network
        and updates the program mid-analysis, so the answer depends on network and platform
        speed. What it added on a real repo was only false edges between unrelated files'
        same-named ``require`` bindings, which the acquired types fold into one symbol.
        """
        return {
            "tsserver": {"useSyntaxServer": "never"},
            "disableAutomaticTypingAcquisition": True,
        }

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
