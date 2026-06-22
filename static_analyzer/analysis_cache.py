"""Static-analysis cache: SHA-tagged pkl persistence + in-memory CFG update helpers.

Two layers, both backing the warm-start incremental flow:

* :class:`StaticAnalysisCache` — the on-disk pickle of a prior
  ``StaticAnalysisResults``, paired with a SHA tag file (``static_analysis.sha``)
  that records the source state the pkl reflects. The tag is a *diff base*
  for the next run, not an exact-match gate.
* :func:`invalidate_files` / :func:`merge_results` — pure in-memory operations
  used by ``update_cfg_for_changed_files``: drop every node/edge/reference
  from a changed file, re-LSP just those files, and merge the fresh state
  back into the kept-from-cache state.

``copy_cache_files`` is the wrapper-side promotion primitive: an opaque
atomic copy of the pkl + sha pair between two artifact directories.
"""

from __future__ import annotations

import copy
import logging
import os
import pickle
import shutil
import sys
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Any

from filelock import FileLock

from static_analyzer.analysis_result import AnalysisData, InvalidatedAnalysis, InvalidatedEdge
from static_analyzer.graph import Edge
from static_analyzer.lsp_client.diagnostics import FileDiagnosticsMap
from static_analyzer.node import Node
from utils import to_absolute_path, to_relative_path

if TYPE_CHECKING:
    from static_analyzer.analysis_result import StaticAnalysisResults

logger = logging.getLogger(__name__)


# Run-artifact filenames. Stored in ``<repo>/.codeboarding/`` (sibling of
# ``analysis.json``), not under ``cache/`` — losing them costs a full LSP
# re-index, so they're not safe to wipe with the rest of the cache.
STATIC_ANALYSIS_PKL = "static_analysis.pkl"
STATIC_ANALYSIS_SHA = "static_analysis.sha"
STATIC_ANALYSIS_LOCK = "static_analysis.lock"
# Legacy location ``StaticAnalysisCache`` wrote to before the run-artifact
# split. Kept for one-time read fallback so CLI users transition smoothly.
_LEGACY_PKL_NAME = "static_analysis_results.pkl"
_LEGACY_CACHE_SUBDIR = "cache"
# Tag file format prefix; bump if the on-disk pickle layout changes.
# v2: StaticAnalysisResults switched from dict-of-dicts to LanguageResults
# dataclass storage. v1 pickles will be treated as cache misses and re-run.
_TAG_VERSION = "v2"


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
        portable = copy.copy(result)
        # Drop runtime-only warm-start context before deep-copying/pickling the cache artifact.
        portable.incremental_base_results = None
        portable = copy.deepcopy(portable)
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

    def _legacy_pkl_path(self) -> Path:
        return self.artifact_dir / _LEGACY_CACHE_SUBDIR / _LEGACY_PKL_NAME

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
        the pickle is only unpickled if the SHA matches — protects against
        stale-cache hits when the source has drifted. When ``expected_sha``
        is None, any tag (or no tag at all) is accepted; legacy pickles
        from the previous on-disk layout are also picked up here.
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
            legacy = self._legacy_pkl_path()
            if expected_sha is None and legacy.exists():
                logger.info(
                    "Reading legacy static analysis cache from %s; "
                    "next save will write to the new artifact location.",
                    legacy,
                )
                target = legacy
            else:
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


def invalidate_files(analysis_result: dict[str, Any], changed_files: set[Path]) -> InvalidatedAnalysis:
    """Return a copy of *analysis_result* with every entry from *changed_files* removed.

    Drops nodes whose ``file_path`` is in the change set, cascades edges that
    reference dropped nodes, remembers cross-boundary edges for later LSP
    validation, drops class hierarchies and references from the same files,
    prunes package relations to surviving files, and filters ``source_files`` /
    ``diagnostics`` accordingly. Raises ``ValueError`` if the result has
    dangling edges or references after filtering.
    """
    changed_file_strs = {str(path) for path in changed_files}

    cached = AnalysisData.from_dict(analysis_result)
    call_graph = cached.call_graph
    invalidated_edges: list[InvalidatedEdge] = []
    filtered_cg = call_graph.filter(
        lambda node: node.file_path not in changed_file_strs,
        on_dropped_edge=lambda edge: _collect_invalidated_edge(edge, changed_file_strs, invalidated_edges),
    )

    diagnostics = None
    if cached.diagnostics is not None:
        diagnostics = {fp: diags for fp, diags in cached.diagnostics.items() if fp not in changed_file_strs}

    class_hierarchies = {
        class_name: class_info.copy()
        for class_name, class_info in cached.class_hierarchies.items()
        if class_info.get("file_path", "") not in changed_file_strs
    }

    package_relations: dict[str, Any] = {}
    for package_name, package_info in cached.package_relations.items():
        remaining_files = [f for f in package_info.get("files", []) if f not in changed_file_strs]
        if remaining_files:
            package_relations[package_name] = {**package_info, "files": remaining_files}

    references = [ref for ref in cached.references if ref.file_path not in changed_file_strs]
    source_files = [file_path for file_path in cached.source_files if str(file_path) not in changed_file_strs]

    updated_result = AnalysisData(
        call_graph=filtered_cg,
        class_hierarchies=class_hierarchies,
        package_relations=package_relations,
        references=references,
        source_files=source_files,
        diagnostics=diagnostics,
    )

    _validate_no_dangling_references(updated_result)

    logger.info(
        f"Invalidated {len(changed_files)} files: kept {len(filtered_cg.nodes)} nodes, "
        f"{len(filtered_cg.edges)} edges, {len(updated_result.references)} references"
    )
    return InvalidatedAnalysis(updated_result, invalidated_edges, changed_file_strs)


def merge_results(
    cached_result: AnalysisData,
    new_result: dict[str, Any],
) -> AnalysisData:
    """Union ``cached_result`` (post-invalidation) with ``new_result`` (fresh re-LSP).

    For overlapping keys (same file appearing in both), the new result wins
    for class hierarchies, packages, references, and diagnostics. Call-graph
    nodes from both sides merge; edges from either side that reference
    nodes present in the merged graph are kept.
    """
    new = AnalysisData.from_dict(new_result)
    new_file_paths = {str(path) for path in new.source_files}
    cached_diagnostics = cached_result.diagnostics or {}
    new_diagnostics = new.diagnostics or {}
    merged_diagnostics: FileDiagnosticsMap = {
        fp: diags for fp, diags in cached_diagnostics.items() if fp not in new_file_paths
    }
    merged_diagnostics.update(new_diagnostics)

    merged = AnalysisData(
        call_graph=cached_result.call_graph.union(new.call_graph),
        class_hierarchies={**cached_result.class_hierarchies, **new.class_hierarchies},
        package_relations={**cached_result.package_relations, **new.package_relations},
        references=[ref for ref in cached_result.references if ref.file_path not in new_file_paths] + new.references,
        source_files=[path for path in cached_result.source_files if str(path) not in new_file_paths]
        + new.source_files,
        diagnostics=merged_diagnostics or None,
    )
    return merged


def _collect_invalidated_edge(
    edge: Edge, changed_file_strs: set[str], invalidated_edges: list[InvalidatedEdge]
) -> None:
    src_node = edge.src_node
    dst_node = edge.dst_node
    src_changed = src_node.file_path in changed_file_strs
    dst_changed = dst_node.file_path in changed_file_strs
    if src_changed != dst_changed:
        invalidated_edges.append((edge.get_source(), edge.get_destination(), src_node, dst_node))


def _validate_no_dangling_references(analysis_result: AnalysisData) -> None:
    """Sanity-check: every edge reaches existing nodes, every reference / class /
    package points at a file in ``source_files``. Raises on violations."""
    call_graph = analysis_result.call_graph
    existing_nodes = set(call_graph.nodes.keys())
    source_file_strs = {str(path) for path in analysis_result.source_files}
    errors: list[str] = []

    for edge in call_graph.edges:
        src_name = edge.get_source()
        dst_name = edge.get_destination()
        if src_name not in existing_nodes:
            errors.append(f"Edge source '{src_name}' references non-existent node")
        if dst_name not in existing_nodes:
            errors.append(f"Edge destination '{dst_name}' references non-existent node")

    for ref in analysis_result.references:
        if ref.file_path not in source_file_strs:
            errors.append(f"Reference '{ref.fully_qualified_name}' from '{ref.file_path}' references non-existent file")

    for class_name, class_info in analysis_result.class_hierarchies.items():
        class_file_path = class_info.get("file_path", "")
        if class_file_path and class_file_path not in source_file_strs:
            errors.append(f"Class hierarchy '{class_name}' references non-existent file '{class_file_path}'")

    for package_name, package_info in analysis_result.package_relations.items():
        for package_file in package_info.get("files", []):
            if package_file not in source_file_strs:
                errors.append(f"Package '{package_name}' references non-existent file '{package_file}'")

    if errors:
        msg = "Dangling references after file invalidation:\n" + "\n".join(f"  - {e}" for e in errors)
        logger.error(msg)
        raise ValueError(msg)
