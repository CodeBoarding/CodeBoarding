"""Go language adapter using gopls."""

from __future__ import annotations

from pathlib import Path

from static_analyzer.engine.language_adapter import LanguageAdapter


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

    def get_all_packages(self, source_files: list[Path], project_root: Path) -> set[str]:
        packages: set[str] = set()
        for f in source_files:
            rel = f.relative_to(project_root)
            parent_parts = rel.parent.parts
            if parent_parts and parent_parts[0] != ".":
                packages.add(".".join(parent_parts))
            else:
                packages.add(rel.stem)
        return packages
