"""LSP Diagnostic data classes for representing diagnostic information.

This module provides data structures for LSP diagnostics including positions,
ranges, and structured diagnostic objects.
"""

from dataclasses import dataclass, field


@dataclass
class DiagnosticPosition:
    """A position in a text document (LSP Position)."""

    line: int = 0
    character: int = 0


@dataclass
class DiagnosticRange:
    """A range in a text document (LSP Range)."""

    start: DiagnosticPosition = field(default_factory=DiagnosticPosition)
    end: DiagnosticPosition = field(default_factory=DiagnosticPosition)


@dataclass
class LSPDiagnostic:
    """A structured representation of an LSP diagnostic notification."""

    code: str = ""
    message: str = ""
    severity: int = 1  # LSP severity: 1=Error, 2=Warning, 3=Info, 4=Hint
    tags: list[int] = field(default_factory=list)
    range: DiagnosticRange = field(default_factory=DiagnosticRange)

    @classmethod
    def from_lsp_dict(cls, data: dict) -> "LSPDiagnostic":
        range_info = data.get("range", {})
        start = range_info.get("start", {})
        end = range_info.get("end", {})
        return cls(
            code=str(data.get("code", "")),
            message=data.get("message", ""),
            severity=data.get("severity", 1),
            tags=data.get("tags", []),
            range=DiagnosticRange(
                start=DiagnosticPosition(line=start.get("line", 0), character=start.get("character", 0)),
                end=DiagnosticPosition(line=end.get("line", 0), character=end.get("character", 0)),
            ),
        )

    def dedup_key(self) -> tuple[str, str, int, int]:
        return (
            self.code,
            self.message,
            self.range.start.line,
            self.range.start.character,
        )


# file_path -> list of diagnostics for that file
FileDiagnosticsMap = dict[str, list[LSPDiagnostic]]
