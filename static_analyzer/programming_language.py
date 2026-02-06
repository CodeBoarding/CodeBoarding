import logging
from pathlib import Path

from pydantic import BaseModel, Field

from static_analyzer.constants import Language

logger = logging.getLogger(__name__)


class LanguageConfig(BaseModel):
    """Base configuration class for language-specific settings."""

    model_config = {"frozen": True}  # Make configs immutable


class JavaConfig(LanguageConfig):
    """Java-specific configuration."""

    jdtls_root: Path = Field(description="Path to the JDTLS (Java Language Server) installation directory")


class ProgrammingLanguage:
    def __init__(
        self,
        language: str,
        size: int,
        percentage: float,
        suffixes: list[str],
        server_commands: list[str] | None = None,
        lsp_server_key: str | None = None,
        language_specific_config: LanguageConfig | None = None,
    ):
        self.language = language
        self.size = size
        self.percentage = percentage
        self.suffixes = suffixes
        self.server_commands = server_commands
        # group related languages (e.g., JS, TSX, JSX -> typescript) to the same language server
        self.lsp_server_key = lsp_server_key or language.lower()
        # Store language-specific configuration (e.g., JavaConfig for Java)
        self.language_specific_config = language_specific_config

    def get_suffix_pattern(self) -> list[str]:
        """Generate and return pattern for the file suffixes, to use in .rglob(pattern)"""
        if not self.suffixes:
            return ["*"]
        # Join suffixes with '|' to create a regex pattern
        return [f"*.{suffix.lstrip('.')}" for suffix in self.suffixes]

    def get_language_id(self) -> str:
        # id for the language, used in LSP server
        return self.language.lower().replace(" ", "_")

    def get_server_parameters(self) -> list[str]:
        if not self.server_commands:
            raise ValueError(
                f"No server commands defined for {self.language}. "
                "Please ensure the language is supported and has server commands defined."
            )
        return self.server_commands

    def is_supported_lang(self) -> bool:
        return self.server_commands is not None

    def __hash__(self):
        return hash(self.lsp_server_key)

    def __eq__(self, other):
        if not isinstance(other, ProgrammingLanguage):
            return False
        return self.lsp_server_key == other.lsp_server_key

    def __str__(self):
        return f"ProgrammingLanguage(language={self.language}, lsp_server_key={self.lsp_server_key}, size={self.size}, percentage={self.percentage:.2f}%, suffixes={self.suffixes})"


class ProgrammingLanguageBuilder:
    """Builder to create ProgrammingLanguage instances from tokei output with greedy LSP matching."""

    def __init__(self, lsp_configs: dict):
        self.lsp_configs = lsp_configs
        # Build reverse index: extension -> lsp_config_key
        self._extension_to_lsp: dict[str, str] = {}
        for lsp_server_key, config in lsp_configs.items():
            for ext in config.get("file_extensions", []):
                # Normalize extension (ensure it starts with '.')
                normalized_ext = ext if ext.startswith(".") else f".{ext}"
                self._extension_to_lsp[normalized_ext] = lsp_server_key

    def _find_lsp_server_key(self, tokei_language: str, file_suffixes: set[str]) -> str | None:
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
            normalized_suffix = suffix if suffix.startswith(".") else f".{suffix}"
            if normalized_suffix in self._extension_to_lsp:
                return self._extension_to_lsp[normalized_suffix]

        return None

    def build(
        self,
        tokei_language: str,
        code_count: int,
        percentage: float,
        file_suffixes: set[str],
    ) -> ProgrammingLanguage:
        lsp_server_key = self._find_lsp_server_key(tokei_language, file_suffixes)

        server_commands: list | None = None
        config_suffixes: set[str] = set()
        language_specific_config: LanguageConfig | None = None

        if lsp_server_key and lsp_server_key in self.lsp_configs:
            config = self.lsp_configs[lsp_server_key]
            server_commands = config.get("command")
            config_suffixes = set(config.get("file_extensions", []))

            # Create language-specific config based on the LSP server key
            if lsp_server_key == Language.JAVA and "jdtls_root" in config:
                language_specific_config = JavaConfig(jdtls_root=Path(config["jdtls_root"]))

        # Merge suffixes from tokei and config
        all_suffixes = file_suffixes | config_suffixes

        return ProgrammingLanguage(
            language=tokei_language,
            size=code_count,
            percentage=percentage,
            suffixes=list(all_suffixes),
            server_commands=server_commands,
            lsp_server_key=lsp_server_key,
            language_specific_config=language_specific_config,
        )

    def get_supported_extensions(self) -> set[str]:
        return set(self._extension_to_lsp.keys())
