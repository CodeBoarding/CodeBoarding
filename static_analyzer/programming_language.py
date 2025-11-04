import logging
from typing import List

logger = logging.getLogger(__name__)


class ProgrammingLanguage:
    def __init__(
        self, language: str, size: int, percentage: float, suffixes: List[str], server_commands: List[str] = None
    ):
        self.language = language
        self.size = size
        self.percentage = percentage
        self.suffixes = suffixes
        self.server_commands = server_commands

    def get_suffix_pattern(self) -> list[str]:
        """Generate and return pattern for the file suffixes, to use in .rglob(pattern)"""
        if not self.suffixes:
            return ["*"]
        # Join suffixes with '|' to create a regex pattern
        return [f"*.{suffix.lstrip('.')}" for suffix in self.suffixes]

    def get_language_id(self) -> str:
        # id for the language, used in LSP server
        return self.language.lower().replace(" ", "_")

    def get_server_parameters(self) -> List[str]:
        if not self.server_commands:
            raise ValueError(
                f"No server commands defined for {self.language}. "
                "Please ensure the language is supported and has server commands defined."
            )
        return self.server_commands

    def is_supported_lang(self) -> bool:
        """
        Check if the language is supported by the static analyzer.
        """
        return self.server_commands is not None

    def __str__(self):
        return f"ProgrammingLanguage(language={self.language}, size={self.size}, percentage={self.percentage}, suffixes={self.suffixes})"
