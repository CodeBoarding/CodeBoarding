"""Shared utilities for the engine package."""

from __future__ import annotations

from pathlib import Path
from urllib.parse import unquote, urlparse


def uri_to_path(uri: str) -> Path | None:
    """Convert a file URI to a Path object.

    Returns None for empty strings, non-file URIs, or on parse errors.

    Windows file URIs (``file:///C:/foo/bar``) parse with a leading slash
    before the drive letter. Passing ``/C:/foo`` straight to ``Path`` on
    Windows produces a drive-less absolute path that fails ``relative_to``
    against any real project root, silently dropping LSP references and
    emptying the call graph. We strip the leading slash when the second
    char is a colon so both POSIX and Windows URIs round-trip correctly.
    """
    if not uri:
        return None
    try:
        parsed = urlparse(uri)
        if parsed.scheme != "file":
            return None
        path = unquote(parsed.path)
        if len(path) >= 3 and path[0] == "/" and path[2] == ":":
            path = path[1:]
        return Path(path)
    except Exception:
        return None
