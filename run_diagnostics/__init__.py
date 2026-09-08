"""Run diagnostics: what a completed analysis has to admit about itself.

``models`` holds the wire types and the collector; ``catalog`` holds one
builder per known degradation, wording included. Recorded during a run, read
back from ``metadata.run_diagnostics`` in ``analysis.json``.
"""

from run_diagnostics.models import (
    SCHEMA_VERSION,
    Diagnostic,
    DiagnosticSeverity,
    RunDiagnostics,
    RunDiagnosticsReport,
)

__all__ = [
    "SCHEMA_VERSION",
    "Diagnostic",
    "DiagnosticSeverity",
    "RunDiagnostics",
    "RunDiagnosticsReport",
]
