"""Go language adapter using gopls."""

from __future__ import annotations

import logging
from pathlib import Path

from repo_utils.ignore import RepoIgnoreManager
from static_analyzer.engine.language_adapter import LanguageAdapter

logger = logging.getLogger(__name__)


class GoAdapter(LanguageAdapter):

    @property
    def language(self) -> str:
        return "Go"

    @property
    def file_extensions(self) -> tuple[str, ...]:
        return (".go",)

    @property
    def lsp_command(self) -> list[str]:
        return ["gopls", "serve"]

    @property
    def language_id(self) -> str:
        return "go"

    @property
    def references_batch_size(self) -> int:
        """Larger batches reduce inter-batch idle time for gopls."""
        return 200

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
