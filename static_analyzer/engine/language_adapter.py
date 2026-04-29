"""Abstract base class for language-specific LSP adapters."""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from pathlib import Path

from repo_utils.ignore import RepoIgnoreManager
from static_analyzer.constants import LANGUAGE_EXTENSIONS, Language, NodeType
from static_analyzer.engine.lsp_client import LSPClient
from static_analyzer.engine.lsp_constants import (
    CALLABLE_KINDS,
    CLASS_LIKE_KINDS,
    EdgeStrategy,
)
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
    def language_enum(self) -> Language:
        """The :class:`Language` enum member this adapter handles.

        Drives :attr:`file_extensions` via ``LANGUAGE_EXTENSIONS`` so the
        per-language extension set lives in exactly one place.
        """

    @property
    def file_extensions(self) -> tuple[str, ...]:
        """File extensions for this language.

        Derived from ``LANGUAGE_EXTENSIONS[self.language_enum]`` — override
        only if a subclass needs a narrower set than the canonical one.
        """
        return LANGUAGE_EXTENSIONS[self.language_enum]

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

    def get_lsp_init_options(self, ignore_manager: RepoIgnoreManager | None = None) -> dict:
        """Return LSP initialization options specific to this language server."""
        return {}

    def get_workspace_settings(self) -> dict | None:
        """Return settings to send via ``workspace/didChangeConfiguration``.

        Some LSP servers (e.g. Pyright) ignore ``initializationOptions``
        for certain configuration keys and only respond to settings
        delivered through ``workspace/didChangeConfiguration``.  Override
        this method to provide those settings.

        Returns ``None`` (the default) when no workspace settings are needed.
        """
        return None

    def get_lsp_default_timeout(self) -> int:
        """Return the default per-request timeout in seconds for the LSP client.

        Override for language servers that need more time to index large
        projects before they can respond to requests (e.g., csharp-ls
        loading a Roslyn workspace).  The default (60s) is suitable for
        most servers.
        """
        return 60

    @property
    def wait_for_workspace_ready(self) -> bool:
        """If True, call wait_for_server_ready() after LSP startup.

        Workspace-based servers (e.g., JDTLS, csharp-ls) need to finish
        loading projects before they can respond to requests.  When True,
        the analyzer blocks on wait_for_server_ready() after start().
        """
        return False

    @property
    def probe_before_open(self) -> bool:
        """If True, send the sync probe BEFORE bulk didOpen notifications.

        Workspace-based servers (e.g., csharp-ls) load all files from the
        solution/project automatically.  Sending hundreds of didOpen
        notifications before the workspace is ready can overwhelm them.
        When True, the call graph builder sends the sync probe first to
        wait for workspace loading, then opens files individually as
        needed for analysis.
        """
        return False

    def get_probe_timeout_minimum(self) -> int:
        """Return the minimum probe timeout in seconds for initial indexing.

        The call graph builder sends a sync probe after opening all files
        to wait for the LSP server to finish indexing.  Some servers
        (e.g., csharp-ls loading a Roslyn workspace) need significantly
        more time than the default 300s base.  Override to raise the floor.
        """
        return 0

    def wait_for_diagnostics(self, client: LSPClient) -> None:
        """Block until the LSP server is done publishing diagnostics for didOpen'd files.

        Default no-op: most LSPs (pyright, gopls, intelephense, JDTLS,
        TypeScript) publish diagnostics synchronously during didOpen
        handling, so by the time Phase 1/2 finishes everything is in the
        queue. Adapters whose servers publish *asynchronously* override
        this to either:
          (a) re-use ``wait_for_server_ready`` after ``reset_ready_signal``
              when the LSP has a real readiness signal that flips around
              didOpen processing (rust-analyzer's ``quiescent``); or
          (b) call ``client.wait_for_diagnostics_quiesce`` to debounce on
              publishDiagnostics activity when there is no signal at all
              (csharp-ls).
        """
        return None

    def get_lsp_env(self) -> dict[str, str]:
        """Return extra environment variables for the LSP server process."""
        return {}

    def prepare_project(self, project_root: Path) -> None:
        """Run any pre-LSP project preparation (e.g. dependency restore).

        Default no-op. C# overrides to run ``dotnet restore`` so csharp-ls
        sees a populated ``obj/project.assets.json`` and can resolve
        framework references; otherwise it floods diagnostics with
        ``CS0518: Predefined type System.X is not defined``.
        """
        return None

    def discover_source_files(self, project_root: Path, ignore_manager: RepoIgnoreManager) -> list[Path]:
        """Discover source files for this adapter under a project root.

        Walks the directory tree, skipping paths rejected by
        ``ignore_manager`` and files that don't match this adapter's
        extensions.

        Returns a sorted list of absolute paths.
        """
        project_root = project_root.resolve()
        extensions = set(self.file_extensions)
        files: list[Path] = []

        for path in self._walk(project_root, ignore_manager):
            if path.suffix in extensions:
                files.append(path)

        files.sort()
        if files:
            logger.info("Found %d %s files in %s", len(files), self.language, project_root)
        return files

    def _walk(self, root: Path, ignore_manager: RepoIgnoreManager):
        """Walk directory tree, skipping paths rejected by RepoIgnoreManager."""
        try:
            entries = sorted(root.iterdir())
        except PermissionError:
            return

        for entry in entries:
            if ignore_manager.should_ignore(entry):
                continue
            if entry.is_dir():
                yield from self._walk(entry, ignore_manager)
            elif entry.is_file():
                yield entry

    def is_class_like(self, symbol_kind: int) -> bool:
        return symbol_kind in CLASS_LIKE_KINDS

    def is_callable(self, symbol_kind: int) -> bool:
        return symbol_kind in CALLABLE_KINDS

    def is_reference_worthy(self, symbol_kind: int) -> bool:
        return symbol_kind in (
            CALLABLE_KINDS
            | CLASS_LIKE_KINDS
            | {
                NodeType.VARIABLE,
                NodeType.CONSTANT,
                NodeType.PROPERTY,
                NodeType.FIELD,
                NodeType.ENUM_MEMBER,
            }
        )

    def should_track_for_edges(self, symbol_kind: int) -> bool:
        return symbol_kind in (CALLABLE_KINDS | CLASS_LIKE_KINDS | {NodeType.VARIABLE, NodeType.CONSTANT})

    @property
    def edge_strategy(self) -> EdgeStrategy:
        """Edge-building strategy for Phase 2.

        Default is ``REFERENCES``. Override to ``DEFINITIONS`` for
        languages where references queries are too slow (e.g. Java/JDTLS).
        """
        return EdgeStrategy.REFERENCES

    @property
    def extra_client_capabilities(self) -> dict:
        """Vendor-specific keys to shallow-merge into the LSP ``initialize``
        capabilities (e.g. ``{"experimental": {...}}``). Default ``{}`` keeps
        the shared client free of language-specific opt-ins.
        """
        return {}

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
        return self.build_qualified_name(file_path, symbol_name, symbol_kind, parent_chain, project_root, detail)

    def get_all_packages(self, source_files: list[Path], project_root: Path) -> set[str]:
        """Get all packages that should appear in package dependencies.

        Default: dotted directory path (e.g. ``src.models`` for ``src/models/foo.py``).
        Root-level files use their stem as the package name.
        Override for languages that need different package extraction.
        """
        packages: set[str] = set()
        for f in source_files:
            rel = f.relative_to(project_root)
            parent_parts = rel.parent.parts
            if parent_parts and parent_parts[0] != ".":
                packages.add(".".join(parent_parts))
            else:
                packages.add(rel.stem)
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
