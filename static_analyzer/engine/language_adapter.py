"""Abstract base class for language-specific LSP adapters."""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from pathlib import Path

from static_analyzer.engine.edge_builder import build_edges_via_references
from static_analyzer.engine.lsp_constants import (  # noqa: F401 — re-exported for backward compatibility
    CALLABLE_KINDS,
    CLASS_LIKE_KINDS,
    SYMBOL_KIND_ARRAY,
    SYMBOL_KIND_BOOLEAN,
    SYMBOL_KIND_CLASS,
    SYMBOL_KIND_CONSTANT,
    SYMBOL_KIND_CONSTRUCTOR,
    SYMBOL_KIND_ENUM,
    SYMBOL_KIND_ENUM_MEMBER,
    SYMBOL_KIND_EVENT,
    SYMBOL_KIND_FIELD,
    SYMBOL_KIND_FILE,
    SYMBOL_KIND_FUNCTION,
    SYMBOL_KIND_INTERFACE,
    SYMBOL_KIND_KEY,
    SYMBOL_KIND_METHOD,
    SYMBOL_KIND_MODULE,
    SYMBOL_KIND_NAMESPACE,
    SYMBOL_KIND_NULL,
    SYMBOL_KIND_NUMBER,
    SYMBOL_KIND_OBJECT,
    SYMBOL_KIND_OPERATOR,
    SYMBOL_KIND_PACKAGE,
    SYMBOL_KIND_PROPERTY,
    SYMBOL_KIND_STRING,
    SYMBOL_KIND_STRUCT,
    SYMBOL_KIND_TYPE_PARAMETER,
    SYMBOL_KIND_VARIABLE,
)
from static_analyzer.engine.models import EdgeBuildContext
from utils import get_config

logger = logging.getLogger(__name__)


class LanguageAdapter(ABC):
    """Strategy interface for language-specific behavior."""

    @property
    @abstractmethod
    def language(self) -> str:
        """Language name as it appears in results (e.g., 'Python', 'Go')."""

    @property
    @abstractmethod
    def file_extensions(self) -> tuple[str, ...]:
        """File extensions for this language (e.g., ('.py',))."""

    @property
    @abstractmethod
    def lsp_command(self) -> list[str]:
        """Command to start the LSP server."""

    @property
    def config_key(self) -> str:
        """Key used to look up this language in tool_registry / VSCODE_CONFIG.

        Defaults to ``language_id``.  Override when the config key differs
        (e.g. JavaScript shares the "typescript" config entry).
        """
        return self.language_id

    def get_lsp_command(self, project_root: Path) -> list[str]:
        """Get the LSP command with binary paths resolved from tool_registry.

        Looks up the resolved command for this language in the tool config
        (which checks ~/.codeboarding/servers/ then system PATH).  Falls
        back to the bare command names from ``lsp_command`` if the config
        has no entry for this language.
        """
        lsp_servers = get_config("lsp_servers")
        entry = lsp_servers.get(self.config_key)
        if entry and "command" in entry:
            return list(entry["command"])
        return self.lsp_command

    @property
    def language_id(self) -> str:
        """LSP language identifier for textDocument/didOpen."""
        return self.language.lower()

    def build_qualified_name(
        self,
        file_path: Path,
        symbol_name: str,
        symbol_kind: int,
        parent_chain: list[tuple[str, int]],
        project_root: Path,
        detail: str = "",
    ) -> str:
        """Build the original-casing qualified name for a symbol.

        Default: ``module.parent1.parent2.symbol_name`` where module is the
        dot-joined relative path without suffix.  Override for languages that
        need different logic (Go receiver notation, Java name cleaning, etc.).
        """
        rel = file_path.relative_to(project_root)
        module = ".".join(rel.with_suffix("").parts)
        if parent_chain:
            parents = ".".join(name for name, _ in parent_chain)
            return f"{module}.{parents}.{symbol_name}"
        return f"{module}.{symbol_name}"

    def build_reference_key(self, qualified_name: str) -> str:
        """Build the reference key from a qualified name.

        Preserves original casing so that references in the output match
        the symbol names as they appear in source code.
        """
        return qualified_name

    def extract_package(self, qualified_name: str) -> str:
        """Extract the package/module name from a qualified name.

        Default: first dot-separated component.  Override for languages with
        deeper package structures (TypeScript, PHP, Java).
        """
        return qualified_name.split(".")[0]

    def get_package_for_file(self, file_path: Path, project_root: Path) -> str:
        """Get the package name for a file based on its directory path.

        Root-level files use their stem as the package name to avoid lumping
        everything into a single pseudo-package.  Override for languages with
        different package conventions (e.g. Java src/main/java/...).
        """
        try:
            rel = file_path.relative_to(project_root)
        except ValueError:
            return "external"
        parent_parts = rel.parent.parts
        if parent_parts and parent_parts[0] != ".":
            return ".".join(parent_parts)
        return rel.stem

    def get_lsp_init_options(self) -> dict:
        """Return LSP initialization options specific to this language server."""
        return {}

    def get_excluded_dirs(self) -> set[str]:
        """Return directory names to exclude from file discovery."""
        return {
            ".git",
            "__pycache__",
            ".DS_Store",
            "test",
            "tests",
            "testing",
            "test_data",
            "__tests__",
            "__test__",
            "spec",
            "specs",
        }

    def is_test_file(self, file_path: Path) -> bool:
        """Whether a file is a test file and should be excluded."""
        stem = file_path.stem.lower()
        name = file_path.name.lower()
        return (
            stem.startswith("test_")
            or stem.endswith("_test")
            or stem.endswith(".test")
            or stem.endswith(".spec")
            or name.endswith("_test.go")
        )

    def is_class_like(self, symbol_kind: int) -> bool:
        return symbol_kind in CLASS_LIKE_KINDS

    def is_callable(self, symbol_kind: int) -> bool:
        return symbol_kind in CALLABLE_KINDS

    def is_reference_worthy(self, symbol_kind: int) -> bool:
        return symbol_kind in (
            CALLABLE_KINDS
            | CLASS_LIKE_KINDS
            | {
                SYMBOL_KIND_VARIABLE,
                SYMBOL_KIND_CONSTANT,
                SYMBOL_KIND_PROPERTY,
                SYMBOL_KIND_FIELD,
                SYMBOL_KIND_ENUM_MEMBER,
            }
        )

    def should_track_for_edges(self, symbol_kind: int) -> bool:
        return symbol_kind in (CALLABLE_KINDS | CLASS_LIKE_KINDS | {SYMBOL_KIND_VARIABLE, SYMBOL_KIND_CONSTANT})

    def build_edges(self, ctx: EdgeBuildContext, source_files: list[Path]) -> set[tuple[str, str]]:
        """Phase 2: Build call-graph edges.

        Default uses references-based strategy. Override in language adapters
        that need a different strategy (e.g. definition-based for JDTLS).
        """
        return build_edges_via_references(self, ctx, source_files)

    @property
    def references_batch_size(self) -> int:
        """Max number of references requests to send in a single batch."""
        return 50

    @property
    def references_per_query_timeout(self) -> int:
        """Per-query timeout for batched references. 0 means use the default batch timeout."""
        return 0

    def build_edge_name(
        self,
        file_path: Path,
        symbol_name: str,
        symbol_kind: int,
        parent_chain: list[tuple[str, int]],
        project_root: Path,
        detail: str = "",
    ) -> str:
        """Build the name used in call graph edges. Defaults to qualified_name."""
        return self.build_qualified_name(file_path, symbol_name, symbol_kind, parent_chain, project_root)

    def get_all_packages(self, source_files: list[Path], project_root: Path) -> set[str]:
        """Get all packages that should appear in package dependencies.

        Default: dotted directory path (e.g. ``src.models`` for ``src/models/foo.py``).
        Override for languages that need different package extraction.
        """
        packages: set[str] = set()
        for f in source_files:
            rel = f.relative_to(project_root)
            parts = list(rel.parts[:-1])
            if parts:
                pkg = ".".join(parts)
                packages.add(pkg)
        return packages

    @staticmethod
    def _extract_deep_package(qualified_name: str) -> str:
        """Extract package as all but the last two dot-separated components.

        For languages like TypeScript and PHP where the qualified name is
        ``dir1.dir2.file.Symbol`` and the package is ``dir1.dir2``.
        Falls back to the first component if fewer than 3 parts.
        """
        parts = qualified_name.split(".")
        if len(parts) >= 3:
            return ".".join(parts[:-2])
        if len(parts) >= 2:
            return parts[0]
        return qualified_name

    def _get_hierarchical_packages(self, source_files: list[Path], project_root: Path) -> set[str]:
        """Get all directory prefixes as packages (for TypeScript, PHP, etc.)."""
        packages: set[str] = set()
        for f in source_files:
            rel = f.relative_to(project_root)
            parts = list(rel.parts[:-1])
            for i in range(1, len(parts) + 1):
                packages.add(".".join(parts[:i]))
        return packages
