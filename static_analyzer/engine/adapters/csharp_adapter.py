"""C# language adapter using csharp-ls (Roslyn-based)."""

from __future__ import annotations

import os
import shutil
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
        return ["csharp-ls"]

    @property
    def language_id(self) -> str:
        return "csharp"

    def get_lsp_command(self, project_root: Path) -> list[str]:
        """Resolve csharp-ls, checking ~/.dotnet/tools if not on PATH."""
        cmd = super().get_lsp_command(project_root)
        binary = cmd[0]
        # If the binary resolves on PATH, use as-is
        if shutil.which(binary):
            return cmd
        # Fallback: dotnet global tools directory
        home_tool = Path.home() / ".dotnet" / "tools" / binary
        if home_tool.is_file():
            return [str(home_tool)] + cmd[1:]
        return cmd

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
        """Configure csharp-ls for static analysis.

        Settings are read from the ``csharp`` workspace configuration section.
        """
        return {
            "csharp": {
                "logLevel": "warning",
            },
        }

    def get_workspace_settings(self) -> dict | None:
        return {
            "csharp": {
                "logLevel": "warning",
            },
        }

    @property
    def wait_for_workspace_ready(self) -> bool:
        return True

    @property
    def probe_before_open(self) -> bool:
        """csharp-ls loads all files from the .sln — didOpen before workspace load kills it."""
        return True

    def get_lsp_default_timeout(self) -> int:
        """csharp-ls needs extra time to load Roslyn workspace for large solutions."""
        return 120

    def get_probe_timeout_minimum(self) -> int:
        """Roslyn workspace loading for large .NET solutions can exceed 5 minutes."""
        return 600

    def get_lsp_env(self) -> dict[str, str]:
        """Set DOTNET_ROOT when not already in the environment.

        csharp-ls requires the .NET runtime to be discoverable. On systems
        where the SDK is installed via a package manager (e.g. Homebrew on
        macOS), the ``DOTNET_ROOT`` variable may not be set, causing
        csharp-ls to fail at startup.  This resolves the runtime location
        from the ``dotnet`` binary on PATH.
        """
        if os.environ.get("DOTNET_ROOT"):
            return {}
        dotnet = shutil.which("dotnet")
        if dotnet:
            dotnet_root = Path(dotnet).resolve().parent.parent / "libexec"
            if dotnet_root.is_dir():
                return {"DOTNET_ROOT": str(dotnet_root)}
        return {}

    def is_reference_worthy(self, symbol_kind: int) -> bool:
        """Include namespaces in reference tracking (similar to PHP modules)."""
        return super().is_reference_worthy(symbol_kind) or symbol_kind == NodeType.NAMESPACE

    def get_all_packages(self, source_files: list[Path], project_root: Path) -> set[str]:
        """Get all directory prefixes as packages (namespace-based, like PHP)."""
        return self._get_hierarchical_packages(source_files, project_root)
