"""Go language adapter using gopls."""

from __future__ import annotations

import logging
import re
import shutil
from pathlib import Path

from repo_utils.ignore import RepoIgnoreManager, _ALWAYS_IGNORED_DIRS
from static_analyzer.constants import Language
from static_analyzer.engine.language_adapter import LanguageAdapter

logger = logging.getLogger(__name__)

# Matches patterns like "**/dirname/**" or "**/dirname/"
_RECURSIVE_DIR_RE = re.compile(r"^\*\*/([a-zA-Z0-9_\-]+)(?:/\*\*)?/?$")
# Matches patterns like "dirname/" (bare directory)
_BARE_DIR_RE = re.compile(r"^([a-zA-Z0-9_\-]+)/$")


def _directory_filters_from_ignore_manager(ignore_manager: RepoIgnoreManager | None) -> list[str]:
    """Convert .codeboardingignore directory patterns to gopls directoryFilters.

    Extracts directory-level patterns from the ignore manager's codeboardingignore
    spec and converts them to gopls format (``-**/dirname``). File-level patterns
    (e.g. ``*.test.*``) are skipped — gopls only supports directory filtering.

    Also includes the always-ignored directories (node_modules, build, etc.).
    """
    filters: list[str] = []
    seen: set[str] = set()

    # Always-ignored directories first
    for dirname in sorted(_ALWAYS_IGNORED_DIRS):
        key = dirname.lower()
        if key not in seen:
            seen.add(key)
            filters.append(f"-**/{dirname}")

    if ignore_manager is None:
        return filters

    # Parse the codeboardingignore patterns for directory entries
    for line in ignore_manager._load_codeboardingignore_patterns():
        pattern = line.strip()
        if not pattern or pattern.startswith("#") or pattern.startswith("!"):
            continue

        # Match "**/dirname/**" or "**/dirname/"
        m = _RECURSIVE_DIR_RE.match(pattern)
        if m:
            dirname = m.group(1)
            key = dirname.lower()
            if key not in seen:
                seen.add(key)
                filters.append(f"-**/{dirname}")
            continue

        # Match "dirname/"
        m = _BARE_DIR_RE.match(pattern)
        if m:
            dirname = m.group(1)
            key = dirname.lower()
            if key not in seen:
                seen.add(key)
                filters.append(f"-**/{dirname}")
            continue

    return filters


class GoAdapter(LanguageAdapter):

    @property
    def language(self) -> str:
        return "Go"

    @property
    def language_enum(self) -> Language:
        return Language.GO

    @property
    def lsp_command(self) -> list[str]:
        return ["gopls", "serve"]

    @property
    def language_id(self) -> str:
        return "go"

    def get_lsp_command(self, project_root: Path) -> list[str]:
        """Fail fast if the Go toolchain is missing.

        gopls needs ``go`` on PATH to resolve modules and stdlib — without
        it, indexing silently returns empty results. Mirrors Rust's check.
        """
        if shutil.which("go") is None:
            raise RuntimeError(
                "Go toolchain not found on PATH. gopls requires Go to "
                "resolve modules and the standard library. Install one via "
                "https://go.dev/dl/ and re-run the analysis."
            )
        return super().get_lsp_command(project_root)

    def build_qualified_name(
        self,
        file_path: Path,
        symbol_name: str,
        symbol_kind: int,
        parent_chain: list[tuple[str, int]],
        project_root: Path,
        detail: str = "",
    ) -> str:
        rel = file_path.relative_to(project_root)
        dir_parts = list(rel.parent.parts) if rel.parent != Path(".") else []
        file_stem = rel.stem
        module = ".".join(dir_parts + [file_stem]) if dir_parts else file_stem

        if parent_chain:
            receiver_name, receiver_kind = parent_chain[-1]
            is_pointer = self._is_pointer_receiver(detail, receiver_name)
            if is_pointer:
                return f"{module}.(*{receiver_name}).{symbol_name}"
            else:
                return f"{module}.({receiver_name}).{symbol_name}"
        return f"{module}.{symbol_name}"

    @staticmethod
    def _is_pointer_receiver(detail: str, receiver_name: str) -> bool:
        if not detail:
            return False
        return f"*{receiver_name}" in detail or f"* {receiver_name}" in detail

    def build_reference_key(self, qualified_name: str) -> str:
        """Preserve original casing for Go qualified names."""
        return qualified_name

    def get_lsp_init_options(self, ignore_manager: RepoIgnoreManager | None = None) -> dict:
        """Configure gopls for lower memory usage.

        - ``directoryFilters``: derived from ``.codeboardingignore`` patterns
          so user customizations automatically flow to gopls. Tells gopls to
          skip directories entirely (never loaded into memory).
        - ``analyses``: disables all optional static analyzers. We only need
          documentSymbol and references for call-graph construction. Core
          diagnostics (unused imports/variables) still work because they come
          from the Go type-checker, not from analyzers.
        """
        directory_filters = _directory_filters_from_ignore_manager(ignore_manager)
        # gopls expects flat settings (no "gopls" wrapper) in initializationOptions.
        # The "gopls" nesting seen in editor configs is an editor convention.
        return {
            "directoryFilters": directory_filters,
            "analyses": {
                "all": False,
                # Re-enable unused-code analyzers for dead-code detection.
                # Both are off by default and must be explicitly enabled.
                # unusedparams: flags unused function parameters.
                # unusedfunc: flags unused unexported functions.
                # unusedvariable is intentionally omitted — it only provides
                # quick-fixes for existing compiler errors, not new diagnostics.
                "unusedparams": True,
                "unusedfunc": True,
            },
        }

    def get_workspace_settings(self) -> dict | None:
        # gopls requests settings via workspace/configuration with section "gopls".
        # The response must be a flat settings object (no "gopls" wrapper).
        return {
            "analyses": {
                "unusedparams": True,
                "unusedfunc": True,
            },
            "ui.diagnostic.staticcheck": True,
        }

    def get_lsp_env(self) -> dict[str, str]:
        """Tune Go GC for lower peak memory at the cost of more CPU.

        ``GOGC=50`` makes the garbage collector run more frequently (default
        is 100), roughly halving peak heap size for memory-intensive workloads
        like gopls indexing large repositories.
        Avoids OOM errors on large codebases, especially in constrained environments like CI.
        """
        return {"GOGC": "50"}

    def discover_source_files(self, project_root: Path, ignore_manager: RepoIgnoreManager) -> list[Path]:
        """Discover Go source files, filtering out build-tag-constrained files.

        Files with ``//go:build`` or ``// +build`` directives containing
        negations (``!``) are excluded because gopls cannot resolve package
        metadata for them, which causes errors during cross-reference queries.
        """
        files = super().discover_source_files(project_root, ignore_manager)
        filtered = [f for f in files if not self._has_excluding_build_tag(f)]
        skipped = len(files) - len(filtered)
        if skipped:
            logger.info("Filtered %d Go files with excluding build tags", skipped)
        return filtered

    @staticmethod
    def _has_excluding_build_tag(file_path: Path) -> bool:
        """Check if a Go file has a build constraint with negation."""
        try:
            with open(file_path, "r", errors="replace") as f:
                for line in f:
                    stripped = line.strip()
                    if not stripped or stripped.startswith("//"):
                        if stripped.startswith("//go:build ") or stripped.startswith("// +build "):
                            tag_expr = (
                                stripped.split(" ", 2)[-1]
                                if stripped.startswith("// +build")
                                else stripped[len("//go:build ") :]
                            )
                            if "!" in tag_expr:
                                return True
                        continue
                    # Stop at the first non-comment, non-blank line (typically "package ...")
                    break
        except OSError:
            pass
        return False
