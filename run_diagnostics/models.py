"""What a finished run has to admit about itself.

A run that raises never reaches a consumer with a half-truth: the CLI exits
non-zero and nothing is written. The dangerous case is the run that *finishes*
with less than it should have — a language server that never came up, an LLM
that stopped answering — and hands over an ``analysis.json`` that looks exactly
like a good one. These are the records of that, carried in the document itself
so every surface downstream can say so.
"""

from collections import deque
from enum import Enum

from pydantic import BaseModel, Field

SCHEMA_VERSION = 1


class DiagnosticSeverity(str, Enum):
    """How much of the diagram the reader should distrust."""

    # Structure is missing: components, relations or names that a clean run would have had.
    DEGRADED = "degraded"
    # The diagram is whole; something about how it was produced is still worth knowing.
    NOTICE = "notice"


class Diagnostic(BaseModel):
    """One thing that went wrong without stopping the run.

    ``remedy`` empty means nothing the reader does would have changed the
    outcome; consumers offer "report this" instead of an instruction.
    """

    code: str = Field(description="Stable identifier, e.g. 'static.language_engine_failed'.")
    severity: DiagnosticSeverity
    title: str = Field(description="One line, in the reader's terms.")
    detail: str = Field(description="What is missing from the diagram because of it.")
    remedy: str = Field(default="", description="What the reader can do about it; empty when nothing would.")
    subject: str = Field(default="", description="What it is about (a language, a scope) — dedupes repeats.")
    count: int = Field(default=1, description="How many times this code/subject pair was recorded.")


class RunDiagnosticsReport(BaseModel):
    """The serialized form: what ``metadata.run_diagnostics`` holds."""

    version: int = Field(default=SCHEMA_VERSION)
    degraded: int = Field(default=0, description="Number of degraded entries.")
    notices: int = Field(default=0, description="Number of notice entries.")
    entries: list[Diagnostic] = Field(default_factory=list, description="Degraded first, then notices.")


class RunDiagnostics:
    """Collector for one analysis run.

    A ``deque`` rather than a lock: engines record from a thread pool, and
    ``append`` is thread-safe by contract, which keeps this picklable and
    deep-copyable — it rides on ``StaticAnalysisResults``, which is both.
    Aggregation happens on read so recording stays cheap and non-blocking.
    """

    def __init__(self) -> None:
        self._recorded: deque[Diagnostic] = deque()

    def __bool__(self) -> bool:
        return bool(self._recorded)

    def record(self, diagnostic: Diagnostic) -> None:
        self._recorded.append(diagnostic)

    def absorb(self, other: "RunDiagnostics") -> None:
        """Take over another collector's records (e.g. the static analyzer's)."""
        self._recorded.extend(other._recorded)

    def entries(self) -> list[Diagnostic]:
        """Deduplicated by ``(code, subject)`` with counts summed, degraded first.

        Why dedupe: a server that fails on every file would otherwise bury the
        one entry that explains why half the diagram is missing.
        """
        merged: dict[tuple[str, str], Diagnostic] = {}
        for diagnostic in list(self._recorded):
            key = (diagnostic.code, diagnostic.subject)
            seen = merged.get(key)
            if seen is None:
                merged[key] = diagnostic.model_copy(deep=True)
            else:
                seen.count += diagnostic.count
        return sorted(
            merged.values(),
            key=lambda d: (d.severity != DiagnosticSeverity.DEGRADED, d.code, d.subject),
        )

    def report(self) -> RunDiagnosticsReport:
        entries = self.entries()
        return RunDiagnosticsReport(
            version=SCHEMA_VERSION,
            degraded=sum(1 for e in entries if e.severity == DiagnosticSeverity.DEGRADED),
            notices=sum(1 for e in entries if e.severity == DiagnosticSeverity.NOTICE),
            entries=entries,
        )
