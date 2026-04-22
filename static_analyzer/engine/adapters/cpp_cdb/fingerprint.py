"""Content-hash caching so we don't regenerate an unchanged CDB.

A generated ``compile_commands.json`` stays valid until one of its input
files (the Makefile, BUILD files, configure.ac, …) actually changes.
Running ``make`` or ``bazel aquery`` every analysis would otherwise
dominate wall-clock time on large repos.
"""

from __future__ import annotations

import hashlib
import logging
import os
import tempfile
from collections.abc import Iterable
from pathlib import Path

from static_analyzer.engine.adapters.cpp_cdb.base import CDB_SKIP_DIRS

logger = logging.getLogger(__name__)

_FINGERPRINT_FILENAME = ".fingerprint"


def collect_project_sources(
    project_root: Path,
    extensions: Iterable[str],
    *,
    skip_dirs: Iterable[str] | None = None,
    extra_skip_prefixes: Iterable[str] = (),
) -> list[Path]:
    """Walk ``project_root`` and return source files matching ``extensions``.

    Used by Make/Bazel/Autotools fingerprint builders so adding a new
    ``src/new.cc`` busts the cached CDB even when the Makefile/BUILD
    hasn't changed.

    * ``skip_dirs`` defaults to :data:`CDB_SKIP_DIRS` — node_modules,
      .codeboarding, .git, etc.
    * ``extra_skip_prefixes`` is a tuple of directory-name prefixes to
      skip (e.g. ``("bazel-",)`` so ``bazel-bin`` symlinks are ignored).
    * Symlinked subdirectories are skipped — following them can walk into
      Bazel's output tree and hash megabytes of generated artefacts.
    """
    ext_set = {e.lower() for e in extensions}
    skip = set(skip_dirs) if skip_dirs is not None else set(CDB_SKIP_DIRS)
    prefixes = tuple(extra_skip_prefixes)
    out: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(project_root):
        root = Path(dirpath)
        dirnames[:] = [
            name
            for name in dirnames
            if name not in skip
            and not name.startswith(".")
            and not any(name.startswith(p) for p in prefixes)
            and not (root / name).is_symlink()
        ]
        for filename in filenames:
            if Path(filename).suffix.lower() in ext_set:
                out.append(root / filename)
    return out


def compute_fingerprint(paths: Iterable[Path]) -> str:
    """SHA-256 over the sorted (path, content) pairs of the given files.

    Missing files contribute a sentinel so deleting an input invalidates
    the cache. Directories are skipped (the caller is expected to enumerate
    files explicitly — we don't walk recursively here to avoid surprising
    the caller when a deep tree hashes slowly).
    """
    hasher = hashlib.sha256()
    # Sort canonically so the hash is independent of iteration order.
    for path in sorted(set(paths), key=lambda p: str(p)):
        hasher.update(str(path).encode("utf-8"))
        hasher.update(b"\0")
        try:
            if path.is_file():
                hasher.update(path.read_bytes())
            else:
                hasher.update(b"<missing>")
        except OSError as exc:
            logger.warning("fingerprint: unreadable %s (%s)", path, exc)
            hasher.update(b"<unreadable>")
        hasher.update(b"\0\0")
    return hasher.hexdigest()


def read_cached_fingerprint(cdb_dir: Path) -> str | None:
    """Return the stored fingerprint for a CDB dir, or None if absent."""
    marker = cdb_dir / _FINGERPRINT_FILENAME
    if not marker.is_file():
        return None
    try:
        return marker.read_text(encoding="utf-8").strip() or None
    except OSError as exc:
        logger.warning("fingerprint: could not read %s (%s)", marker, exc)
        return None


def write_cached_fingerprint(cdb_dir: Path, fingerprint: str) -> None:
    """Persist a fingerprint alongside a generated CDB."""
    cdb_dir.mkdir(parents=True, exist_ok=True)
    marker = cdb_dir / _FINGERPRINT_FILENAME
    fd, tmp_name = tempfile.mkstemp(prefix=f".{_FINGERPRINT_FILENAME}.", suffix=".tmp", dir=str(cdb_dir))
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(fingerprint)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, marker)
    except OSError as exc:
        tmp_path.unlink(missing_ok=True)
        logger.warning("fingerprint: could not write %s (%s)", marker, exc)


def delete_cached_fingerprint(cdb_dir: Path) -> None:
    marker = cdb_dir / _FINGERPRINT_FILENAME
    try:
        marker.unlink(missing_ok=True)
    except OSError as exc:
        logger.warning("fingerprint: could not delete %s (%s)", marker, exc)
    if not cdb_dir.is_dir():
        return
    for tmp in cdb_dir.glob(f".{_FINGERPRINT_FILENAME}.*.tmp"):
        tmp.unlink(missing_ok=True)
