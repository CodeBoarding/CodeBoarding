"""Persistence helpers for incremental CLI history."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

HISTORY_FILENAME = "incremental_history.jsonl"


def _history_path(output_dir: str | Path) -> Path:
    return Path(output_dir) / HISTORY_FILENAME


def record_incremental_history(output_dir: str | Path, entry: dict[str, Any]) -> Path:
    """Append one incremental run entry to the history log."""
    path = _history_path(output_dir)
    path.parent.mkdir(parents=True, exist_ok=True)

    payload = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        **entry,
    }
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, sort_keys=True))
        handle.write("\n")
    return path


def load_incremental_history(output_dir: str | Path) -> list[dict[str, Any]]:
    """Load incremental run entries from the history log."""
    path = _history_path(output_dir)
    if not path.exists():
        return []

    entries: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        entries.append(json.loads(line))
    return entries
