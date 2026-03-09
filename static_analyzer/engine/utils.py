"""Shared utilities for the engine package."""

from __future__ import annotations

from pathlib import Path
from urllib.parse import unquote, urlparse


def uri_to_path(uri: str) -> Path | None:
    """Convert a file URI to a Path object.

    Returns None for empty strings, non-file URIs, or on parse errors.
    """
    if not uri:
        return None
    try:
        parsed = urlparse(uri)
        if parsed.scheme == "file":
            return Path(unquote(parsed.path))
        return None
    except Exception:
        return None
