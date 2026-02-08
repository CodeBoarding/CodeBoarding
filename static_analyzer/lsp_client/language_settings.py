"""Language-specific LSP server settings.

This module contains configuration settings for various LSP servers to enable
features like unused code detection, diagnostics, and other language-specific
capabilities.
"""

from static_analyzer.constants import Language


def get_language_settings(language_id: str) -> dict | None:
    """Get language-specific settings to enable unused code diagnostics.

    Args:
        language_id: The language identifier (e.g., 'python', 'typescript')

    Returns:
        Settings dictionary for the language server, or None if not applicable.
    """
    lang_id = language_id.lower()

    if lang_id == Language.PYTHON:
        # Pyright/Pylance settings to enable unused code detection
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
                }
            }
        }
    elif lang_id in (Language.TYPESCRIPT, Language.JAVASCRIPT):
        # TypeScript/JavaScript settings
        return {
            "typescript": {
                "preferences": {
                    "includePackageJsonAutoImports": "on",
                }
            },
            "javascript": {
                "preferences": {
                    "includePackageJsonAutoImports": "on",
                }
            },
        }
    elif lang_id == Language.GO:
        # gopls settings
        return {
            "gopls": {
                "ui.diagnostic.annotations": {
                    "bounds": True,
                    "escape": True,
                    "inline": True,
                    "nil": True,
                    "unusedParams": True,
                },
                "ui.diagnostic.staticcheck": True,
            }
        }
    elif lang_id == Language.JAVA:
        # Eclipse JDT Language Server settings
        return {
            "java": {
                "settings": {
                    "org.eclipse.jdt.core.compiler.problem.unusedImport": "warning",
                    "org.eclipse.jdt.core.compiler.problem.unusedLocal": "warning",
                    "org.eclipse.jdt.core.compiler.problem.unusedPrivateMember": "warning",
                    "org.eclipse.jdt.core.compiler.problem.unusedTypeParameter": "warning",
                    "org.eclipse.jdt.core.compiler.problem.deadCode": "warning",
                    "org.eclipse.jdt.core.compiler.problem.redundantSuperinterface": "warning",
                }
            }
        }
    elif lang_id == Language.PHP:
        # Intelephense settings
        return {
            "intelephense": {
                "diagnostics": {
                    "unusedSymbols": True,
                    "unusedUseStatements": True,
                }
            }
        }

    return None
