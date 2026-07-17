"""Python language adapter using Pyright."""

from __future__ import annotations

from collections.abc import Collection
from functools import lru_cache
from pathlib import Path

from repo_utils.ignore import RepoIgnoreManager
from static_analyzer.constants import Language
from static_analyzer.engine.language_adapter import LanguageAdapter
from static_analyzer.engine.models import ImportDependency

# ``src/<package>`` is the Python Packaging Authority's common "src layout".
# We only use it as a fallback for namespace packages; ordinary packages are
# discovered from their __init__.py chain without relying on this directory name.
PYTHON_SOURCE_ROOT_DIRECTORY = "src"
PYTHON_PACKAGE_INITIALIZER = "__init__.py"


class PythonAdapter(LanguageAdapter):
    @property
    def language(self) -> str:
        return "Python"

    @property
    def language_enum(self) -> Language:
        return Language.PYTHON

    @property
    def lsp_command(self) -> list[str]:
        return ["pyright-langserver", "--stdio"]

    @property
    def language_id(self) -> str:
        return "python"

    def get_lsp_init_options(self, ignore_manager: RepoIgnoreManager | None = None) -> dict:
        return {
            "python": {
                "analysis": {
                    "autoSearchPaths": True,
                    "diagnosticMode": "workspace",
                    "useLibraryCodeForTypes": True,
                },
            },
        }

    def get_workspace_settings(self) -> dict | None:
        # All six rules default to "none" under basic mode.  Raising them to
        # "warning" makes pyright include the diagnostic code (e.g.
        # reportUnusedImport) which is needed for dead-code categorization.
        return {
            "python": {
                "analysis": {
                    "typeCheckingMode": "basic",
                    "diagnosticSeverityOverrides": {
                        "reportUnusedImport": "warning",
                        "reportUnusedVariable": "warning",
                        "reportUnusedFunction": "warning",
                        "reportUnusedClass": "warning",
                        "reportUnusedParameter": "warning",
                        "reportUnreachable": "warning",
                    },
                },
            },
        }

    def resolve_import_target(
        self,
        declaration: ImportDependency,
        source_files: Collection[Path],
        project_root: Path,
    ) -> Path | None:
        """Resolve Python imports through an exact project module index."""
        candidates = tuple(
            sorted((path if path.is_absolute() else project_root / path).resolve() for path in source_files)
        )
        module_to_paths, path_to_module = self._module_index(project_root.resolve(), candidates)

        source_path = Path(declaration.source_file)
        source = (source_path if source_path.is_absolute() else project_root / source_path).resolve()
        module = declaration.declared_module.strip()
        if module.startswith("."):
            source_module = path_to_module.get(source)
            if source_module is None:
                return None
            package_parts = source_module.split(".")
            if source.name != PYTHON_PACKAGE_INITIALIZER:
                package_parts = package_parts[:-1]
            leading = len(module) - len(module.lstrip("."))
            parent_count = leading - 1
            if parent_count > len(package_parts):
                return None
            package_parts = package_parts[: len(package_parts) - parent_count]
            suffix = module.lstrip(".")
            if suffix:
                package_parts.extend(suffix.split("."))
            module = ".".join(package_parts)

        matches = module_to_paths.get(module, set())
        return next(iter(matches)) if len(matches) == 1 else None

    @staticmethod
    @lru_cache(maxsize=4)
    def _module_index(
        project_root: Path,
        source_files: tuple[Path, ...],
    ) -> tuple[dict[str, set[Path]], dict[Path, str]]:
        candidates = set(source_files)
        module_to_paths: dict[str, set[Path]] = {}
        path_to_module: dict[Path, str] = {}
        for path in source_files:
            module = PythonAdapter._module_name(path, project_root, candidates)
            if not module:
                continue
            module_to_paths.setdefault(module, set()).add(path)
            path_to_module[path] = module
        return module_to_paths, path_to_module

    @staticmethod
    def _module_name(path: Path, project_root: Path, source_files: set[Path]) -> str:
        package_parts: list[str] = []
        current = path.parent
        while current != project_root and (current / PYTHON_PACKAGE_INITIALIZER).resolve() in source_files:
            package_parts.insert(0, current.name)
            current = current.parent

        if package_parts:
            parts = [*package_parts, path.stem]
        else:
            source_root = next(
                (
                    parent
                    for parent in path.parents
                    if parent != project_root and parent.name == PYTHON_SOURCE_ROOT_DIRECTORY
                ),
                project_root,
            )
            parts = list(path.relative_to(source_root).with_suffix("").parts)

        if parts and parts[-1] == Path(PYTHON_PACKAGE_INITIALIZER).stem:
            parts.pop()
        return ".".join(parts)
