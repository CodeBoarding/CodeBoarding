import logging
from typing import List

logger = logging.getLogger(__name__)


class ProgrammingLanguage:
    def __init__(self, language: str, size: int, percentage: str, suffixes: List[str]):
        self.language = language
        self.size = size
        self.percentage = percentage
        self.suffixes = suffixes

    def get_suffix_pattern(self) -> str:
        """Generate and return pattern for the file suffixes, to use in .rglob(pattern)"""
        if not self.suffixes:
            return "*"
        # Join suffixes with '|' to create a regex pattern
        return "|".join([f"*.{suffix.lstrip('.')}" for suffix in self.suffixes])

    def get_language_id(self) -> str:
        ids = {'Python': 'python', 'TypeScript': 'typescript', 'JavaScript': 'javascript', }
        return ids.get(self.language, self.language.lower().replace(" ", "_"))

    def get_server_parameters(self) -> List[str]:
        server_params = {
            'Python': ['pyright-langserver', '--stdio'],
            'TypeScript': ['typescript-language-server', '--stdio'],
            'JavaScript': ['typescript-language-server', '--stdio'],
        }

        if self.language not in server_params:
            logger.warning("[ProgrammingLanguage] No server parameters found for language: %s", self.language)

        return server_params.get(self.language)

    def is_supported_lang(self) -> bool:
        """
        Check if the language is supported by the static analyzer.
        """
        supported_languages = ['Python', 'TypeScript', 'JavaScript']
        return self.language in supported_languages
