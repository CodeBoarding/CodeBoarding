"""Telling a language server that stopped answering from one that has nothing to say."""

from __future__ import annotations

from collections.abc import Callable
from typing import Generic, TypeVar

from static_analyzer.exceptions import StaticAnalysisFatalError

CHECK_EVERY = 100
"""Files between two re-asks of the first question the server answered."""
MIN_SILENT_FILES = 50
"""Files that may all come back without a symbol before the silence means the server is gone."""
MIN_SILENT_CALL_SITES = 1000
"""Call sites that may all come back without a definition before the silence means the server is gone."""

Question = TypeVar("Question")


class LanguageServerStalledError(StaticAnalysisFatalError):
    """The server process is up, but it no longer answers what it answered earlier in the run.

    Why fatal: a wrapper server (typescript-language-server over tsserver) keeps replying after the
    server behind it dies, and its empty replies read as "nothing declared here". The run would then
    cache a graph missing most of its symbols or calls and report success.
    """


class Canary(Generic[Question]):
    """The first question the server answered, asked again every ``CHECK_EVERY`` files."""

    def __init__(self, phase: str, ask: Callable[[Question], bool]) -> None:
        self._phase = phase
        self._ask = ask
        self._question: Question | None = None
        self._files = 0

    @property
    def heard(self) -> bool:
        """Whether the server has answered any question in this phase."""
        return self._question is not None

    def answered(self, question: Question) -> None:
        """Remember *question* if it is the first one the server answered."""
        if self._question is None:
            self._question = question

    def tick(self) -> None:
        """Count one more file, re-asking the question every ``CHECK_EVERY`` of them."""
        self._files += 1
        if self._files % CHECK_EVERY == 0:
            self.check()

    def finish(self) -> None:
        """Re-ask once more at the end of a phase of at least ``CHECK_EVERY`` files.

        Why not on a shorter phase: an incremental run reads a handful of files, and one more request
        per phase there buys nothing a full run would miss.
        """
        if self._files >= CHECK_EVERY and self._files % CHECK_EVERY:
            self.check()

    def check(self) -> None:
        """Raise if the server no longer answers the question it answered first."""
        if self._question is not None and not self._ask(self._question):
            raise LanguageServerStalledError(
                f"{self._phase}: the language server stopped answering after {self._files} files; it no longer "
                f"answers {self._question}, which it answered earlier in this run"
            )

    def require_heard(self, asked: int, threshold: int, what: str) -> None:
        """Raise if *asked* questions, at least *threshold* of them, all went unanswered."""
        if asked >= threshold and not self.heard:
            raise LanguageServerStalledError(
                f"{self._phase}: the language server answered none of {asked} {what}; it is running but "
                "not answering"
            )
