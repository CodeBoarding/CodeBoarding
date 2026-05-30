import logging
from pathlib import Path

from pydantic import BaseModel, Field

from static_analyzer.constants import (
    LANGUAGE_TO_LSP_CONFIG_KEY,
    SOURCE_EXTENSION_TO_LANGUAGE,
    TOKEI_LANGUAGE_TO_LSP_CONFIG_KEY,
    Language,
)

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
        # Build reverse index: extension -> lsp_config_key. Sourced from
        # ``SOURCE_EXTENSION_TO_LANGUAGE`` + ``LANGUAGE_TO_LSP_CONFIG_KEY`` so the
        # extension set cannot drift from ``LANGUAGE_EXTENSIONS`` (which is what
        # adapters consume). Only emit entries whose lsp_config_key is actually
        # present in ``lsp_configs`` so callers passing trimmed configs still work.
        self._extension_to_lsp: dict[str, str] = {}
        for ext, language in SOURCE_EXTENSION_TO_LANGUAGE.items():
            lsp_key = LANGUAGE_TO_LSP_CONFIG_KEY[language]
            if lsp_key in lsp_configs:
                self._extension_to_lsp[ext] = lsp_key

    def _find_lsp_server_key(self, tokei_language: str, file_suffixes: set[str]) -> str | None:
        """
        Find the LSP config key for a tokei language by matching file extensions.

        Args:
            tokei_language: Language name from tokei output (e.g., "JavaScript", "TSX")
            file_suffixes: Set of file suffixes from tokei reports

        Returns:
            LSP config key if found, None otherwise
        """

        normalized = tokei_language.lower()
        # Direct match on lsp_configs keys (e.g. "Python" -> "python").
        if normalized in self.lsp_configs:
            return normalized
        # Tokei names that don't lowercase into an lsp key on their own
        # ("C", "C++", "C Header", "C++ Header" all -> "cpp"; "TSX"/"JSX"
        # -> "typescript"). Why: keeps the direct path working for C-only
        # repos before the extension-based fallback even runs.
        aliased = TOKEI_LANGUAGE_TO_LSP_CONFIG_KEY.get(normalized)
        if aliased and aliased in self.lsp_configs:
            return aliased

        # Fallback: try matching by file extensions.
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
