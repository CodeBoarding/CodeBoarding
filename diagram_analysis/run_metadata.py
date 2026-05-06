"""Run-mode tag and baseline lookup for incremental analysis.

Baseline resolution lives at the live ``analysis.json``'s top-level
``snapshotCommit`` field (stamped by the wrapper at promote time).
The CLI and wrapper both read it via :func:`last_successful_commit`.
"""

from __future__ import annotations

import json
from enum import StrEnum
from pathlib import Path

from utils import ANALYSIS_FILENAME


class RunMode(StrEnum):
    """Mode tag for analyze runs (wire payloads, TypedDicts, CLI emits)."""

    FULL = "full"
    INCREMENTAL = "incremental"


def last_successful_commit(output_dir: Path) -> str | None:
    """Return the snapshot commit recorded in the live ``analysis.json``.

    Reads the top-level ``snapshotCommit`` field. Returns ``None`` when
    the file is absent, unreadable, not a JSON object, or the field is
    missing or empty — callers treat this as "no baseline available".
    """
    path = Path(output_dir) / ANALYSIS_FILENAME
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    sha = data.get("snapshotCommit")
    return sha if isinstance(sha, str) and sha else None
