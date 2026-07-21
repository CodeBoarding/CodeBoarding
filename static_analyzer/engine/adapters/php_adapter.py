"""PHP language adapter using Intelephense."""

from __future__ import annotations

import os
from pathlib import Path

from repo_utils.ignore import RepoIgnoreManager
from static_analyzer.constants import Language, NodeType
from static_analyzer.engine.language_adapter import LanguageAdapter


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        value = int(raw.strip())
    except ValueError:
        return default
    return value if value > 0 else default


class PHPAdapter(LanguageAdapter):

    @property
    def probe_before_open(self) -> bool:
        """Integphense is happier when indexing readiness is checked before opens."""
        return True

    @property
    def interleave_did_open_with_symbols(self) -> bool:
        """Keep overlay creation and symbol extraction in lockstep for large PHP repos."""
        return True

    @property
    def references_batch_size(self) -> int:
        """Lower batch concurrency to avoid overloading Intelephense on big projects."""
        return _env_int("CODEBOARDING_PHP_REFERENCES_BATCH_SIZE", 10)

    @property
    def references_per_query_timeout(self) -> int:
        """Keep reference queries bounded to avoid long request tails."""
        return _env_int("CODEBOARDING_PHP_REFERENCES_QUERY_TIMEOUT", 10)

    @property
    def language(self) -> str:
        return "PHP"

    @property
    def language_enum(self) -> Language:
        return Language.PHP

    @property
    def lsp_command(self) -> list[str]:
        return ["intelephense", "--stdio"]

    @property
    def language_id(self) -> str:
        return "php"

    def extract_package(self, qualified_name: str) -> str:
        return self._extract_deep_package(qualified_name)

    def get_lsp_init_options(self, ignore_manager: RepoIgnoreManager | None = None) -> dict:
        return {"clearCache": False}

    def get_workspace_settings(self) -> dict | None:
        # unusedSymbols is already true by default but we set it defensively.
        return {
            "intelephense": {
                "diagnostics": {
                    "unusedSymbols": True,
                }
            }
        }

    def is_reference_worthy(self, symbol_kind: int) -> bool:
        return super().is_reference_worthy(symbol_kind) or symbol_kind == NodeType.MODULE

    def get_all_packages(self, source_files: list[Path], project_root: Path) -> set[str]:
        return self._get_hierarchical_packages(source_files, project_root)
