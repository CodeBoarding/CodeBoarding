"""Python language adapter using Pyright."""

from __future__ import annotations

from repo_utils.ignore import RepoIgnoreManager
from static_analyzer.engine.language_adapter import LanguageAdapter


class PythonAdapter(LanguageAdapter):

    @property
    def language(self) -> str:
        return "Python"

    @property
    def file_extensions(self) -> tuple[str, ...]:
        return (".py",)

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
        # Pyright ignores diagnosticSeverityOverrides in initializationOptions
        # and only responds to workspace/didChangeConfiguration.  At the
        # default "hint" severity pyright omits the diagnostic code from
        # publishDiagnostics; raising to "warning" makes it include codes
        # like reportUnusedImport, which are needed for fine-grained
        # dead-code categorization.
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
