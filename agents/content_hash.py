"""Content fingerprinting helpers — a leaf module with no analysis imports.

Standalone (no heavy analysis imports) so clustering, the JSON layer, and the
wrapper can all import it without an ``analysis_json`` <-> ``cluster_methods_mixin``
cycle. ``splitlines()`` folds line-ending-only edits — accepted for change
detection. Decode/encode both use ``surrogateescape`` so invalid UTF-8 bytes stay
distinct (``replace`` would collapse them to U+FFFD and mask real changes).
"""

import hashlib
import os
from pathlib import Path
from typing import NamedTuple

from repo_utils.ignore import RepoIgnoreManager

SOURCE_ENCODING = "utf-8"
SOURCE_DECODE_ERRORS = "surrogateescape"

# Repo-relative path -> cached lines (empty list when unreadable). Shared line
# cache threaded through the hashing helpers to avoid re-reading the same file.
SourceCache = dict[str, list[str]]


class MethodRef(NamedTuple):
    """A method's identity across files: keyed by path so a qualified name that
    collides across files can't borrow the wrong file's hash/span."""

    file_path: str
    qualified_name: str


class MethodSpan(NamedTuple):
    """A method's 1-based inclusive line range in its file."""

    start_line: int
    end_line: int


def read_source_lines(repo_dir: Path, rel_path: str, cache: SourceCache) -> list[str]:
    """Read and cache a file's lines (repo-relative path). Empty list if unreadable."""
    if rel_path not in cache:
        try:
            cache[rel_path] = (
                (repo_dir / rel_path).read_text(encoding=SOURCE_ENCODING, errors=SOURCE_DECODE_ERRORS).splitlines()
            )
        except OSError:
            cache[rel_path] = []
    return cache[rel_path]


def hash_method_body(lines: list[str], start_line: int, end_line: int) -> str:
    """Truncated SHA-256 of source lines [start_line-1:end_line]. '' when unavailable.

    Why '' on an out-of-range span: a stale line range must not hash to a stable
    but meaningless value that compares equal across unrelated code.
    """
    if start_line < 1 or end_line < start_line or end_line > len(lines):
        return ""
    body = "\n".join(lines[start_line - 1 : end_line])
    return hashlib.sha256(body.encode(SOURCE_ENCODING, errors=SOURCE_DECODE_ERRORS)).hexdigest()[:16]


def hash_whole_file(lines: list[str]) -> str:
    """Truncated SHA-256 of the entire file's lines. '' when the file has no lines."""
    if not lines:
        return ""
    return hashlib.sha256("\n".join(lines).encode(SOURCE_ENCODING, errors=SOURCE_DECODE_ERRORS)).hexdigest()[:16]


def tree_hash_from_file_hashes(file_hashes: dict[str, str]) -> str:
    """SHA-256 over sorted ``path:hash`` lines. '' when no files carry a hash.

    Files with an unknown ('') hash are skipped so a partial read can't silently
    collide two different trees. Public so the wrapper can reproduce
    ``source_tree_hash`` from a fingerprint map.
    """
    parts = [f"{path}:{digest}" for path, digest in sorted(file_hashes.items()) if digest]
    if not parts:
        return ""
    return hashlib.sha256("\n".join(parts).encode(SOURCE_ENCODING)).hexdigest()


def hash_repo_source_files(repo_dir: Path) -> dict[str, str]:
    """Fingerprint every non-ignored file under *repo_dir* as ``{posix_path: sha16}``.

    Covers the whole analyzable tree (docs, configs, unclustered source), not just
    files that landed in a component, so a consumer that fingerprints the working
    tree — the wrapper — reproduces the same map. Ignored directories are pruned
    during the walk, so ``.git`` / ``node_modules`` are never descended into.
    """
    ignore = RepoIgnoreManager(repo_dir)
    result: dict[str, str] = {}
    for dirpath, dirnames, filenames in os.walk(repo_dir):
        base = Path(dirpath)
        dirnames[:] = [d for d in dirnames if not ignore.should_ignore((base / d).relative_to(repo_dir))]
        for name in filenames:
            rel = (base / name).relative_to(repo_dir)
            if ignore.should_ignore(rel):
                continue
            try:
                lines = (base / name).read_text(encoding=SOURCE_ENCODING, errors=SOURCE_DECODE_ERRORS).splitlines()
            except OSError:
                continue
            digest = hash_whole_file(lines)
            if digest:
                result[rel.as_posix()] = digest
    return result


def compute_source_tree_hash(repo_dir: Path) -> str:
    """The canonical source-tree version key: whole-repo walk, hashed and aggregated."""
    return tree_hash_from_file_hashes(hash_repo_source_files(repo_dir))
