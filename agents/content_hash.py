"""Content fingerprinting helpers — a dependency-free leaf module.

Why standalone: these are imported by clustering, the incremental agent, the
JSON layer, and the wrapper. Keeping them free of heavy analysis imports avoids
the ``analysis_json`` <-> ``cluster_methods_mixin`` import cycle.

``splitlines()`` normalizes line endings, so a trailing-newline-only or CRLF/LF
edit is not detected — an accepted trade-off for change detection at this level.
"""

import hashlib
from pathlib import Path


def read_source_lines(repo_dir: Path, rel_path: str, cache: dict[str, list[str] | None]) -> list[str] | None:
    """Read and cache a file's lines (repo-relative path). None if unreadable."""
    if rel_path not in cache:
        try:
            cache[rel_path] = (repo_dir / rel_path).read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            cache[rel_path] = None
    return cache[rel_path]


def hash_method_body(lines: list[str] | None, start_line: int, end_line: int) -> str:
    """Truncated SHA-256 of source lines [start_line-1:end_line]. '' when unavailable.

    Returns '' (the unavailable sentinel) rather than hashing a partial slice when
    the span falls outside the file, so a stale line range can't produce a stable
    but meaningless hash that compares equal across unrelated code.
    """
    if lines is None or start_line < 1 or end_line < start_line or end_line > len(lines):
        return ""
    body = "\n".join(lines[start_line - 1 : end_line])
    return hashlib.sha256(body.encode("utf-8")).hexdigest()[:16]


def hash_whole_file(lines: list[str] | None) -> str:
    """Truncated SHA-256 of the entire file's lines. '' when unavailable."""
    if lines is None:
        return ""
    return hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()[:16]
