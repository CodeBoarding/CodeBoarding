"""Mode tag for analyze runs (wire payloads, TypedDicts, CLI emits)."""

from __future__ import annotations

from enum import StrEnum


class RunMode(StrEnum):
    FULL = "full"
    INCREMENTAL = "incremental"
