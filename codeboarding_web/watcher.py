"""Watch a repo's source tree and fire a callback on debounced source changes."""

import asyncio
import logging
from collections.abc import Callable
from pathlib import Path

from watchfiles import awatch
from watchfiles.main import Change

from static_analyzer.constants import SOURCE_EXTENSION_TO_LANGUAGE

logger = logging.getLogger(__name__)


class RepoWatcher:
    """Fire *on_change* when a source file under *repo_path* changes."""

    def __init__(self, repo_path: Path, output_dir: Path, on_change: Callable[[], None]) -> None:
        self.repo_path = repo_path
        self.output_dir = output_dir.resolve()
        self.on_change = on_change

    def _should_watch(self, path: str) -> bool:
        """True for source files outside the output dir and .git."""
        p = Path(path).resolve()
        if self.output_dir in p.parents or p == self.output_dir:
            return False
        if ".git" in p.parts:
            return False
        return p.suffix.lower() in SOURCE_EXTENSION_TO_LANGUAGE

    def _watch_filter(self, change: Change, path: str) -> bool:
        return self._should_watch(path)

    async def run(self, stop_event: asyncio.Event) -> None:
        """Watch until *stop_event* is set, firing on_change per debounced batch."""
        async for _ in awatch(self.repo_path, watch_filter=self._watch_filter, stop_event=stop_event):
            try:
                self.on_change()
            except Exception:
                logger.exception("watch on_change failed")
