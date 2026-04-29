"""Python language adapter using Pyright."""

from __future__ import annotations

from repo_utils.ignore import RepoIgnoreManager
from static_analyzer.constants import Language
from static_analyzer.engine.language_adapter import LanguageAdapter


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
