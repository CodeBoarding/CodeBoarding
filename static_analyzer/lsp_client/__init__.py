"""LSP Client module for static analysis.

This module provides LSP client implementations for different programming languages,
along with diagnostic data structures and language-specific settings.
"""

from static_analyzer.lsp_client.diagnostics import (
    DiagnosticPosition,
    DiagnosticRange,
    FileDiagnosticsMap,
    LSPDiagnostic,
)
