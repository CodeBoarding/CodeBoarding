"""Minimal Bash symbol support using bash-language-server."""

from static_analyzer.config import Language
from static_analyzer.engine.language_adapter import LanguageAdapter


class BashAdapter(LanguageAdapter):
    @property
    def language(self) -> str:
        return "Bash"

    @property
    def language_enum(self) -> Language:
        return Language.BASH

    @property
    def lsp_command(self) -> list[str]:
        return ["bash-language-server", "start"]
