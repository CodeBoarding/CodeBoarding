"""Content fingerprinting helpers — a dependency-free leaf module.

Standalone (no heavy analysis imports) so clustering, the JSON layer, and the
wrapper can all import it without an ``analysis_json`` <-> ``cluster_methods_mixin``
cycle. ``splitlines()`` folds line-ending-only edits — accepted for change
detection. Decode/encode both use ``surrogateescape`` so invalid UTF-8 bytes stay
distinct (``replace`` would collapse them to U+FFFD and mask real changes).
"""

import hashlib
from pathlib import Path
from typing import NamedTuple

SOURCE_ENCODING = "utf-8"
SOURCE_DECODE_ERRORS = "surrogateescape"

# Repo-relative path -> cached lines (None when unreadable). Shared line cache
# threaded through the hashing helpers to avoid re-reading the same file.
SourceCache = dict[str, list[str] | None]


class MethodRef(NamedTuple):
    """A method's identity across files: keyed by path so a qualified name that
    collides across files can't borrow the wrong file's hash/span."""

    file_path: str
    qualified_name: str


class MethodSpan(NamedTuple):
    """A method's 1-based inclusive line range in its file."""

    start_line: int
    end_line: int


def read_source_lines(repo_dir: Path, rel_path: str, cache: SourceCache) -> list[str] | None:
    """Read and cache a file's lines (repo-relative path). None if unreadable."""
    if rel_path not in cache:
        try:
            cache[rel_path] = (
                (repo_dir / rel_path).read_text(encoding=SOURCE_ENCODING, errors=SOURCE_DECODE_ERRORS).splitlines()
            )
        except OSError:
            cache[rel_path] = None
    return cache[rel_path]


def hash_method_body(lines: list[str] | None, start_line: int, end_line: int) -> str:
    """Truncated SHA-256 of source lines [start_line-1:end_line]. '' when unavailable.

    Why '' on an out-of-range span: a stale line range must not hash to a stable
    but meaningless value that compares equal across unrelated code.
    """
    if lines is None or start_line < 1 or end_line < start_line or end_line > len(lines):
        return ""
    body = "\n".join(lines[start_line - 1 : end_line])
    return hashlib.sha256(body.encode(SOURCE_ENCODING, errors=SOURCE_DECODE_ERRORS)).hexdigest()[:16]


def hash_whole_file(lines: list[str] | None) -> str:
    """Truncated SHA-256 of the entire file's lines. '' when unavailable."""
    if lines is None:
        return ""
    return hashlib.sha256("\n".join(lines).encode(SOURCE_ENCODING, errors=SOURCE_DECODE_ERRORS)).hexdigest()[:16]
