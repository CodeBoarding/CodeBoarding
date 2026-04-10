"""Shared utilities for the engine package."""

from __future__ import annotations

import functools
import platform
from pathlib import Path
from urllib.parse import unquote, urlparse

_IS_WINDOWS = platform.system() == "Windows"


@functools.lru_cache(maxsize=4096)
def uri_to_path(uri: str) -> Path | None:
    """Convert a file URI to a Path; None for empty, non-file, or parse errors."""
    if not uri:
        return None
    try:
        parsed = urlparse(uri)
        if parsed.scheme != "file":
            return None
        path = unquote(parsed.path)
        # Strip the leading slash on Windows-style URIs (``/C:/foo`` -> ``C:/foo``).
        if len(path) >= 3 and path[0] == "/" and path[2] == ":":
            path = path[1:]
        if _IS_WINDOWS:
            return Path(path).resolve()
        return Path(path)
    except Exception:
        return None
