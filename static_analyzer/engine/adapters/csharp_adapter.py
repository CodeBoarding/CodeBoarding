"""C# language adapter using OmniSharp."""

from __future__ import annotations

from pathlib import Path

from repo_utils.ignore import RepoIgnoreManager
from static_analyzer.constants import NodeType
from static_analyzer.engine.language_adapter import LanguageAdapter


class CSharpAdapter(LanguageAdapter):

    @property
    def language(self) -> str:
        return "CSharp"

    @property
    def file_extensions(self) -> tuple[str, ...]:
        return (".cs",)

    @property
    def lsp_command(self) -> list[str]:
        return ["OmniSharp", "-lsp"]

    @property
    def language_id(self) -> str:
        return "csharp"

    def build_qualified_name(
        self,
        file_path: Path,
        symbol_name: str,
        symbol_kind: int,
        parent_chain: list[tuple[str, int]],
        project_root: Path,
        detail: str = "",
    ) -> str:
        """Build qualified name, deduplicating filename/class like Java adapter.

        C# convention: one primary type per file, filename matches the type name.
        Without deduplication, ``Services/UserService.cs`` containing class
        ``UserService`` would produce ``Services.UserService.UserService`` instead
        of ``Services.UserService``.
        """
        rel = file_path.relative_to(project_root)
        module = ".".join(rel.with_suffix("").parts)

        if parent_chain:
            module_last = module.rsplit(".", 1)[-1] if "." in module else module
            effective_parents = list(parent_chain)
            if effective_parents and effective_parents[0][0] == module_last:
                effective_parents = effective_parents[1:]

            if effective_parents:
                parents = ".".join(name for name, _ in effective_parents)
                return f"{module}.{parents}.{symbol_name}"
        return f"{module}.{symbol_name}"

    def extract_package(self, qualified_name: str) -> str:
        """Extract namespace as all-but-last-two dot-separated components.

        For ``Services.Auth.AuthService.Login`` the package is ``Services.Auth``.
        """
        return self._extract_deep_package(qualified_name)

    def get_lsp_init_options(self, ignore_manager: RepoIgnoreManager | None = None) -> dict:
        """Configure OmniSharp for static analysis.

        Disables formatting-related features and enables analyzers for
        unused code detection.
        """
        return {
            "RoslynExtensionsOptions": {
                "enableAnalyzersSupport": True,
                "enableDecompilationSupport": False,
            },
            "FormattingOptions": {
                "enableEditorConfigSupport": False,
            },
        }

    def get_workspace_settings(self) -> dict | None:
        return {
            "omnisharp": {
                "enableRoslynAnalyzers": True,
                "enableEditorConfigSupport": False,
            }
        }

    def is_reference_worthy(self, symbol_kind: int) -> bool:
        """Include namespaces in reference tracking (similar to PHP modules)."""
        return super().is_reference_worthy(symbol_kind) or symbol_kind == NodeType.NAMESPACE

    def get_all_packages(self, source_files: list[Path], project_root: Path) -> set[str]:
        """Get all directory prefixes as packages (namespace-based, like PHP)."""
        return self._get_hierarchical_packages(source_files, project_root)
