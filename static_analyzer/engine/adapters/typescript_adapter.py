"""TypeScript and JavaScript language adapter using typescript-language-server."""

from __future__ import annotations

from collections.abc import Collection
from pathlib import Path

from static_analyzer.constants import Language
from static_analyzer.engine.language_adapter import LanguageAdapter
from static_analyzer.engine.models import ImportDependency

TYPESCRIPT_PACKAGE_ENTRY_STEM = "index"


class TypeScriptAdapter(LanguageAdapter):

    @property
    def language(self) -> str:
        return "TypeScript"

    @property
    def language_enum(self) -> Language:
        return Language.TYPESCRIPT

    @property
    def lsp_command(self) -> list[str]:
        return ["typescript-language-server", "--stdio"]

    @property
    def language_id(self) -> str:
        return "typescript"

    def extract_package(self, qualified_name: str) -> str:
        return self._extract_deep_package(qualified_name)

    def get_all_packages(self, source_files: list[Path], project_root: Path) -> set[str]:
        return self._get_hierarchical_packages(source_files, project_root)

    def resolve_import_target(
        self,
        declaration: ImportDependency,
        source_files: Collection[Path],
        project_root: Path,
    ) -> Path | None:
        """Resolve relative TypeScript imports and directory entry modules."""
        module = declaration.declared_module.strip()
        if not module.startswith(("./", "../")):
            return super().resolve_import_target(declaration, source_files, project_root)

        source_path = Path(declaration.source_file)
        source = (source_path if source_path.is_absolute() else project_root / source_path).resolve()
        path_base = (source.parent / module).resolve()
        candidates = {(path if path.is_absolute() else project_root / path).resolve() for path in source_files}
        matches = {
            candidate
            for suffix in set(self.file_extensions)
            for candidate in (
                path_base if path_base.suffix == suffix else path_base.with_suffix(suffix),
                path_base / f"{TYPESCRIPT_PACKAGE_ENTRY_STEM}{suffix}",
            )
            if candidate in candidates
        }
        return matches.pop() if len(matches) == 1 else None


class JavaScriptAdapter(TypeScriptAdapter):

    @property
    def language(self) -> str:
        return "JavaScript"

    @property
    def language_enum(self) -> Language:
        return Language.JAVASCRIPT

    @property
    def language_id(self) -> str:
        return "javascript"

    @property
    def config_key(self) -> str:
        return "typescript"
