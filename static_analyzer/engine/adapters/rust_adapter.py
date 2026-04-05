"""Rust language adapter using rust-analyzer."""

from __future__ import annotations

from pathlib import Path

from static_analyzer.engine.language_adapter import LanguageAdapter


class RustAdapter(LanguageAdapter):

    @property
    def language(self) -> str:
        return "Rust"

    @property
    def file_extensions(self) -> tuple[str, ...]:
        return (".rs",)

    @property
    def lsp_command(self) -> list[str]:
        return ["rust-analyzer"]

    @property
    def language_id(self) -> str:
        return "rust"

    def build_qualified_name(
        self,
        file_path: Path,
        symbol_name: str,
        symbol_kind: int,
        parent_chain: list[tuple[str, int]],
        project_root: Path,
        detail: str = "",
    ) -> str:
        """Build a qualified name, collapsing ``mod.rs`` into its parent directory.

        In Rust, ``src/models/mod.rs`` defines the ``models`` module — the
        ``mod`` file stem is an implementation detail, not part of the
        logical module path.  Stripping it produces
        ``src.models.User`` instead of ``src.models.mod.User``.
        """
        rel = file_path.relative_to(project_root)
        parts = list(rel.with_suffix("").parts)
        if parts and parts[-1] == "mod":
            parts.pop()
        module = ".".join(parts) if parts else rel.stem

        if parent_chain:
            parents = ".".join(name for name, _ in parent_chain)
            return f"{module}.{parents}.{symbol_name}"
        return f"{module}.{symbol_name}"

    def build_reference_key(self, qualified_name: str) -> str:
        """Preserve original casing for Rust qualified names."""
        return qualified_name
