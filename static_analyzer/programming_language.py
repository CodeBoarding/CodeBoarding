import logging
import os
from typing import List

logger = logging.getLogger(__name__)


class ProgrammingLanguage:
    def __init__(self, language: str, size: int, percentage: float, suffixes: List[str]):
        self.language = language
        self.size = size
        self.percentage = percentage
        self.suffixes = suffixes

    def get_suffix_pattern(self) -> str:
        """Generate and return pattern for the file suffixes, to use in .rglob(pattern)"""
        if not self.suffixes:
            return "*"
        # Join suffixes with '|' to create a regex pattern
        return [f"*.{suffix.lstrip('.')}" for suffix in self.suffixes]

    def get_language_id(self) -> str:
        ids = {'Python': 'python', 'TypeScript': 'typescript', 'JavaScript': 'javascript', }
        return ids.get(self.language, self.language.lower().replace(" ", "_"))

    def get_server_parameters(self) -> List[str]:
        server_params = {
            'Python': [f'pyright-langserver', '--stdio'],
            'TypeScript': [f'{os.environ["SERVER_LOCATION"]}/typescript/node_modules/.bin/typescript-language-server',
                           '--stdio', '--log-level=2'],
        }

        if self.language not in server_params:
            logger.warning("[ProgrammingLanguage] No server parameters found for language: %s", self.language)

        return server_params.get(self.language)

    def is_supported_lang(self) -> bool:
        """
        Check if the language is supported by the static analyzer.
        """
        supported_languages = ['Python', 'TypeScript']
        return self.language in supported_languages

    def __str__(self):
        return f"ProgrammingLanguage(language={self.language}, size={self.size}, percentage={self.percentage}, suffixes={self.suffixes})"
