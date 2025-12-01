import logging
from typing import List, Optional, Dict, Set

logger = logging.getLogger(__name__)


class ProgrammingLanguage:
    def __init__(
            self, language: str, size: int, percentage: float, suffixes: List[str],
            server_commands: List[str] = None, lsp_key: Optional[str] = None
    ):
        self.language = language
        self.size = size
        self.percentage = percentage
        self.suffixes = suffixes
        self.server_commands = server_commands
        # lsp_key is used for grouping related languages (e.g., JS, TSX, JSX -> typescript)
        self.lsp_key = lsp_key or language.lower()

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

    def __hash__(self):
        """Hash based on lsp_key for deduplication."""
        return hash(self.lsp_key)

    def __eq__(self, other):
        """Equality based on lsp_key."""
        if not isinstance(other, ProgrammingLanguage):
            return False
        return self.lsp_key == other.lsp_key

    def __str__(self):
        return f"ProgrammingLanguage(language={self.language}, lsp_key={self.lsp_key}, size={self.size}, percentage={self.percentage:.2f}%, suffixes={self.suffixes})"


class ProgrammingLanguageBuilder:
    """Builder to create ProgrammingLanguage instances from tokei output with greedy LSP matching."""

    def __init__(self, lsp_configs: dict):
        """
        Initialize builder with LSP server configurations.

        Args:
            lsp_configs: Dictionary of LSP server configurations from config file
        """
        self.lsp_configs = lsp_configs
        # Build reverse index: extension -> lsp_config_key
        self._extension_to_lsp: Dict[str, str] = {}
        for lsp_key, config in lsp_configs.items():
            for ext in config.get("file_extensions", []):
                # Normalize extension (ensure it starts with '.')
                normalized_ext = ext if ext.startswith('.') else f'.{ext}'
                self._extension_to_lsp[normalized_ext] = lsp_key

    def _find_lsp_config_key(self, tokei_language: str, file_suffixes: Set[str]) -> Optional[str]:
        """
        Find the LSP config key for a tokei language by matching file extensions.

        Args:
            tokei_language: Language name from tokei output (e.g., "JavaScript", "TSX")
            file_suffixes: Set of file suffixes from tokei reports

        Returns:
            LSP config key if found, None otherwise
        """

        # Try direct match with lsp_configs keys
        normalized = tokei_language.lower()
        if normalized in self.lsp_configs:
            return normalized

        # Fallback: try matching by file extensions
        for suffix in file_suffixes:
            normalized_suffix = suffix if suffix.startswith('.') else f'.{suffix}'
            if normalized_suffix in self._extension_to_lsp:
                return self._extension_to_lsp[normalized_suffix]

        return None

    def build(
            self,
            tokei_language: str,
            code_count: int,
            percentage: float,
            file_suffixes: Set[str]
    ) -> ProgrammingLanguage:
        """
        Build a ProgrammingLanguage instance from tokei output.

        Args:
            tokei_language: Language name from tokei (e.g., "JavaScript", "TSX")
            code_count: Lines of code count
            percentage: Percentage of total codebase
            file_suffixes: Set of file suffixes extracted from tokei reports

        Returns:
            ProgrammingLanguage instance with LSP config if available
        """
        lsp_key = self._find_lsp_config_key(tokei_language, file_suffixes)

        server_commands = None
        config_suffixes: Set[str] = set()

        if lsp_key and lsp_key in self.lsp_configs:
            config = self.lsp_configs[lsp_key]
            server_commands = config.get("command")
            config_suffixes = set(config.get("file_extensions", []))

        # Merge suffixes from tokei and config
        all_suffixes = file_suffixes | config_suffixes

        return ProgrammingLanguage(
            language=tokei_language,
            size=code_count,
            percentage=percentage,
            suffixes=list(all_suffixes),
            server_commands=server_commands,
            lsp_key=lsp_key,
        )

    def get_supported_extensions(self) -> Set[str]:
        """Return set of all supported file extensions."""
        return set(self._extension_to_lsp.keys())
