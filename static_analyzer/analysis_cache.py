"""SHA-tagged ProgramGraph artifact persistence."""

from __future__ import annotations

import copy
import logging
import os
import pickle
import shutil
import sys
import tempfile
from pathlib import Path

from filelock import FileLock

from repo_utils.path_utils import to_absolute_path, to_relative_path
from static_analyzer.analysis_result import StaticAnalysisResults

logger = logging.getLogger(__name__)


# Run-artifact filenames. Stored in ``<repo>/.codeboarding/`` (sibling of
# ``analysis.json``), not under ``cache/`` — losing them costs a full LSP
# re-index, so they're not safe to wipe with the rest of the cache.
STATIC_ANALYSIS_PKL = "static_analysis.pkl"
STATIC_ANALYSIS_SHA = "static_analysis.sha"
STATIC_ANALYSIS_LOCK = "static_analysis.lock"
# Tag file format prefix; bump if the on-disk pickle layout changes.
# v4 includes imported symbols and deterministic language-specific module targets.
_TAG_VERSION = "v4"


class StaticAnalysisCache:
    """Reader/writer for the persistent static-analysis run artifact.

    Owns ``static_analysis.pkl`` (the relativised ``StaticAnalysisResults``
    pickle) and ``static_analysis.sha`` (a tag file recording the source
    SHA the pickle reflects). The artifact dir is the same directory that
    holds ``analysis.json``; it is *not* the wipeable ``cache/`` dir.
    """

    def __init__(self, artifact_dir: Path, repo_root: Path):
        self.artifact_dir = artifact_dir
        self.repo_root = repo_root.resolve()

    def _to_relative(self, path: str) -> str:
        return to_relative_path(path, self.repo_root)

    def _to_absolute(self, path: str) -> str:
        return to_absolute_path(path, self.repo_root)

    def _relativize(self, result: "StaticAnalysisResults") -> "StaticAnalysisResults":
        """Return a copy of result with all file paths made repo-relative."""
        portable = copy.deepcopy(result)
        for lang_data in portable.results.values():
            lang_data.visit_paths(self._to_relative)
        portable.diagnostics = {
            lang: {self._to_relative(fp): diags for fp, diags in file_map.items()}
            for lang, file_map in portable.diagnostics.items()
        }
        return portable

    def _absolutize(self, result: "StaticAnalysisResults") -> "StaticAnalysisResults":
        """Expand all repo-relative file paths in result to absolute paths."""
        for lang_data in result.results.values():
            lang_data.visit_paths(self._to_absolute)
        result.diagnostics = {
            lang: {self._to_absolute(fp): diags for fp, diags in file_map.items()}
            for lang, file_map in result.diagnostics.items()
        }
        return result

    @property
    def pkl_path(self) -> Path:
        return self.artifact_dir / STATIC_ANALYSIS_PKL

    @property
    def sha_path(self) -> Path:
        return self.artifact_dir / STATIC_ANALYSIS_SHA

    @property
    def lock_path(self) -> Path:
        return self.artifact_dir / STATIC_ANALYSIS_LOCK

    def read_tag_sha(self) -> str | None:
        """Return the source SHA the pkl was saved at, or None if absent/unparsable.

        Format on disk: ``<version>\\n<sha>\\n``. Unknown versions return
        ``None`` so callers treat them as a cache miss without unpickling.

        Role: the SHA is a **diff base**, not an exact-match gate. The
        warm-start flow loads the pkl regardless of the tag value, then asks
        ``git diff <tag_sha>..HEAD`` for the file list to re-LSP. Pure
        all-or-nothing callers can still use ``get(expected_sha=...)``.
        """
        if not self.sha_path.exists():
            return None
        with FileLock(self.lock_path, timeout=30):
            return self._read_tag_sha_unlocked()

    def _read_tag_sha_unlocked(self) -> str | None:
        try:
            text = self.sha_path.read_text(encoding="utf-8").strip()
        except (OSError, FileNotFoundError):
            return None
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        if len(lines) < 2:
            return None
        version, sha = lines[0], lines[-1]
        if version != _TAG_VERSION:
            logger.info(f"Static analysis tag has unknown version {version!r}; treating as cache miss")
            return None
        return sha

    def load_with_sha(self) -> "tuple[StaticAnalysisResults, str] | None":
        """Load the pkl and its tag SHA together; returns ``None`` if either is absent.

        Used by the warm-start flow: the SHA is needed as a git diff base
        (see ``read_tag_sha``) so the caller can compute "what changed since
        this pkl was saved" and bring the cached CFG up to date in memory.

        Differs from ``get(expected_sha=...)``: this never gates on the SHA,
        it just hands it back along with the loaded results.
        """
        if not self.artifact_dir.exists():
            return None
        with FileLock(self.lock_path, timeout=30):
            cached_sha = self._read_tag_sha_unlocked()
            if cached_sha is None:
                return None
            results = self._get_unlocked()
            if results is None:
                return None
            return results, cached_sha

    def get(self, expected_sha: str | None = None) -> "StaticAnalysisResults | None":
        """Load the cached results, or None if absent/invalid/SHA-mismatched.

        When ``expected_sha`` is provided, the tag file is read first and
        the pickle is only unpickled if the SHA matches — protecting against
        stale-cache hits when the source has drifted.
        """
        if not self.artifact_dir.exists():
            return None
        with FileLock(self.lock_path, timeout=30):
            return self._get_unlocked(expected_sha=expected_sha)

    def _get_unlocked(self, expected_sha: str | None = None) -> "StaticAnalysisResults | None":
        if expected_sha is not None:
            cached_sha = self._read_tag_sha_unlocked()
            if cached_sha is None:
                return None
            if cached_sha != expected_sha:
                logger.info(
                    "Static analysis cache SHA mismatch (cached=%s, expected=%s); skipping",
                    cached_sha,
                    expected_sha,
                )
                return None

        target = self.pkl_path
        if not target.exists():
            return None

        try:
            with open(target, "rb") as f:
                result = pickle.load(f)
            result = self._absolutize(result)
            logger.info(f"Loaded static analysis from cache: {target}")
            return result
        except Exception as e:
            logger.warning(f"Failed to load static analysis cache: {e}")
            return None

    def save(self, result: "StaticAnalysisResults", source_sha: str | None = None) -> None:
        """Save the result with repo-relative paths and a sibling SHA tag.

        ``source_sha`` is the canonical identifier of the source state this
        pickle reflects (e.g. a git tree SHA over HEAD + dirty overlay).
        Stored in the sibling ``static_analysis.sha`` tag so future loads
        can SHA-gate before paying the unpickle cost. Saving without a
        SHA writes the pickle but leaves the tag absent — callers that
        ``get(expected_sha=...)`` will then miss the cache.
        """
        self.artifact_dir.mkdir(parents=True, exist_ok=True)

        with FileLock(self.lock_path, timeout=30):
            portable = self._relativize(result)
            data = pickle.dumps(portable)
            size_mb = sys.getsizeof(data) / (1024 * 1024)
            logger.info(f"Static analysis cache size: {size_mb:.2f} MB")

            temp_fd, temp_path = tempfile.mkstemp(dir=self.artifact_dir, suffix=".pkl.tmp")
            try:
                with open(temp_fd, "wb") as f:
                    f.write(data)
                    # Ensure bytes are durable before the atomic replace.
                    f.flush()
                    os.fsync(f.fileno())
                Path(temp_path).replace(self.pkl_path)
                logger.info(f"Saved static analysis to cache: {self.pkl_path}")
            except Exception as e:
                Path(temp_path).unlink(missing_ok=True)
                logger.warning(f"Failed to save static analysis cache: {e}")
                return

            # Write the sibling tag last so a partially-written pkl never gets a
            # SHA stamp; readers that miss the tag treat it as no-cache.
            if source_sha is not None:
                tag_text = f"{_TAG_VERSION}\n{source_sha}\n"
                tag_fd, tag_tmp = tempfile.mkstemp(dir=self.artifact_dir, suffix=".sha.tmp")
                try:
                    with open(tag_fd, "w", encoding="utf-8", newline="\n") as f:
                        f.write(tag_text)
                        f.flush()
                        os.fsync(f.fileno())
                    Path(tag_tmp).replace(self.sha_path)
                except Exception as e:
                    Path(tag_tmp).unlink(missing_ok=True)
                    # Drop any old tag rather than pair it with the new pkl.
                    try:
                        self.sha_path.unlink()
                    except (OSError, FileNotFoundError):
                        pass
                    logger.warning(f"Failed to write SHA tag, dropped stale tag to avoid mismatch: {e}")
            elif self.sha_path.exists():
                # No SHA provided this run; drop any stale tag so the next
                # SHA-gated read doesn't accidentally accept a mismatched pickle.
                try:
                    self.sha_path.unlink()
                except OSError:
                    pass


def copy_cache_files(src_dir: Path, dest_dir: Path) -> bool:
    """Copy the static-analysis pkl + sha pair from *src_dir* to *dest_dir*.

    Treats the cache as an opaque file pair (no unpickle, no relativization).
    Both files must exist in *src_dir*; a partial source is a no-op. Source
    and destination locks keep readers from seeing a mixed pkl/tag generation.
    Returns True iff both files were installed.
    """
    src_pkl = src_dir / STATIC_ANALYSIS_PKL
    src_sha = src_dir / STATIC_ANALYSIS_SHA
    if not src_dir.exists():
        return False

    dest_pkl = dest_dir / STATIC_ANALYSIS_PKL
    dest_sha = dest_dir / STATIC_ANALYSIS_SHA
    with FileLock(src_dir / STATIC_ANALYSIS_LOCK, timeout=30):
        if not src_pkl.exists() or not src_sha.exists():
            if src_pkl.exists() != src_sha.exists():
                logger.warning(
                    "Source dir %s has %s without its sibling; refusing to copy partial cache",
                    src_dir,
                    STATIC_ANALYSIS_PKL if src_pkl.exists() else STATIC_ANALYSIS_SHA,
                )
            return False

        dest_dir.mkdir(parents=True, exist_ok=True)
        with FileLock(dest_dir / STATIC_ANALYSIS_LOCK, timeout=30):
            try:
                _atomic_copy(src_pkl, dest_pkl)
            except OSError as e:
                logger.warning("Failed to copy %s into %s: %s", STATIC_ANALYSIS_PKL, dest_dir, e)
                return False
            try:
                _atomic_copy(src_sha, dest_sha)
            except OSError as e:
                logger.warning("Failed to copy %s into %s: %s", STATIC_ANALYSIS_SHA, dest_dir, e)
                dest_pkl.unlink(missing_ok=True)
                dest_sha.unlink(missing_ok=True)
                return False
            return True


def _atomic_copy(src: Path, dest: Path) -> None:
    """Copy *src* into place at *dest* via tmp+rename so readers see all-or-nothing."""
    fd, tmp_name = tempfile.mkstemp(prefix=f".{dest.name}.", dir=dest.parent)
    tmp_path = Path(tmp_name)
    os.close(fd)
    try:
        shutil.copy2(src, tmp_path)
        # fsync the freshly-copied bytes before the rename commits, so a crash
        # between rename and writeback can't leave the directory entry pointing
        # at a not-yet-durable inode.
        with open(tmp_path, "rb") as f:
            os.fsync(f.fileno())
        tmp_path.replace(dest)
    except Exception:
        tmp_path.unlink(missing_ok=True)
        raise
