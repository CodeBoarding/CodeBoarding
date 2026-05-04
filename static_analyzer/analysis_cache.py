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

from static_analyzer.graph import CallGraph
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
        result = copy.deepcopy(result)
        for lang_data in result.results.values():
            lang_data.visit_paths(self._to_relative)
        result.diagnostics = {
            lang: {self._to_relative(fp): diags for fp, diags in file_map.items()}
            for lang, file_map in result.diagnostics.items()
        }
        return result

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

    def read_tag_sha(self) -> str | None:
        """Return the source SHA the pkl was saved at, or None if absent/unparsable.

        Format on disk: ``<version>\\n<sha>\\n``. Unknown versions return
        ``None`` so callers treat them as a cache miss without unpickling.

        Role: the SHA is a **diff base**, not an exact-match gate. The
        warm-start flow loads the pkl regardless of the tag value, then asks
        ``git diff <tag_sha>..HEAD`` for the file list to re-LSP. Pure
        all-or-nothing callers can still use ``get(expected_sha=...)``.
        """
        try:
            text = self.sha_path.read_text().strip()
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
        cached_sha = self.read_tag_sha()
        if cached_sha is None:
            return None
        results = self.get()  # SHA-less get -> accepts any tag (or no tag).
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
        if expected_sha is not None:
            cached_sha = self.read_tag_sha()
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

        portable = self._relativize(result)
        data = pickle.dumps(portable)
        size_mb = sys.getsizeof(data) / (1024 * 1024)
        logger.info(f"Static analysis cache size: {size_mb:.2f} MB")

        temp_fd, temp_path = tempfile.mkstemp(dir=self.artifact_dir, suffix=".pkl.tmp")
        try:
            with open(temp_fd, "wb") as f:
                f.write(data)
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
                with open(tag_fd, "w") as f:
                    f.write(tag_text)
                Path(tag_tmp).replace(self.sha_path)
            except Exception as e:
                Path(tag_tmp).unlink(missing_ok=True)
                logger.warning(f"Failed to write static analysis SHA tag: {e}")
        elif self.sha_path.exists():
            # No SHA provided this run — drop any stale tag so the next
            # SHA-gated read doesn't accidentally accept a mismatched pickle.
            try:
                self.sha_path.unlink()
            except OSError:
                pass


def copy_cache_files(src_dir: Path, dest_dir: Path) -> bool:
    """Atomically copy the static-analysis pkl + sha pair from *src_dir* to *dest_dir*.

    Treats the cache as an opaque file pair (no unpickle, no relativization).
    Both files must exist in *src_dir*; a partial source is a no-op so a
    half-written cache cannot be promoted. The pair is installed atomically:
    if the second copy fails, the first is rolled back so a reader never
    sees a pkl without its tag (or vice versa). Returns True iff both files
    were installed.
    """
    src_pkl = src_dir / STATIC_ANALYSIS_PKL
    src_sha = src_dir / STATIC_ANALYSIS_SHA
    if not src_pkl.exists() or not src_sha.exists():
        if src_pkl.exists() != src_sha.exists():
            logger.warning(
                "Source dir %s has %s without its sibling; refusing to copy partial cache",
                src_dir,
                STATIC_ANALYSIS_PKL if src_pkl.exists() else STATIC_ANALYSIS_SHA,
            )
        return False

    dest_pkl = dest_dir / STATIC_ANALYSIS_PKL
    dest_sha = dest_dir / STATIC_ANALYSIS_SHA
    dest_dir.mkdir(parents=True, exist_ok=True)
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
        return False
    return True


def _atomic_copy(src: Path, dest: Path) -> None:
    """Copy *src* into place at *dest* via tmp+rename so readers see all-or-nothing."""
    fd, tmp_name = tempfile.mkstemp(prefix=f".{dest.name}.", dir=dest.parent)
    tmp_path = Path(tmp_name)
    os.close(fd)
    try:
        shutil.copy2(src, tmp_path)
        tmp_path.replace(dest)
    except Exception:
        tmp_path.unlink(missing_ok=True)
        raise


def invalidate_files(analysis_result: dict[str, Any], changed_files: set[Path]) -> dict[str, Any]:
    """Return a copy of *analysis_result* with every entry from *changed_files* removed.

    Drops nodes whose ``file_path`` is in the change set, cascades edges that
    reference dropped nodes, drops class hierarchies and references from the
    same files, prunes package relations to surviving files, and filters
    ``source_files`` / ``diagnostics`` accordingly. Raises ``ValueError`` if
    the result has dangling edges or references after filtering.
    """
    changed_file_strs = {str(path) for path in changed_files}

    updated_result: dict[str, Any] = {
        "call_graph": CallGraph(),
        "class_hierarchies": {},
        "package_relations": {},
        "references": [],
        "source_files": [],
    }

    if "diagnostics" in analysis_result:
        updated_result["diagnostics"] = {
            fp: diags for fp, diags in analysis_result["diagnostics"].items() if fp not in changed_file_strs
        }

    call_graph: CallGraph = analysis_result["call_graph"]
    removed_nodes: set[str] = set()
    kept_nodes: set[str] = set()
    for node_name, node in call_graph.nodes.items():
        if node.file_path in changed_file_strs:
            removed_nodes.add(node_name)
        else:
            updated_result["call_graph"].add_node(node)
            kept_nodes.add(node_name)

    removed_edges = 0
    kept_edges = 0
    for edge in call_graph.edges:
        src_name = edge.get_source()
        dst_name = edge.get_destination()
        if src_name in kept_nodes and dst_name in kept_nodes:
            try:
                updated_result["call_graph"].add_edge(src_name, dst_name)
                kept_edges += 1
            except ValueError as e:
                logger.warning(f"Failed to add edge {src_name} -> {dst_name}: {e}")
                removed_edges += 1
        else:
            removed_edges += 1

    class_hierarchies: dict[str, Any] = analysis_result["class_hierarchies"]
    for class_name, class_info in class_hierarchies.items():
        if class_info.get("file_path", "") not in changed_file_strs:
            updated_result["class_hierarchies"][class_name] = class_info.copy()

    package_relations: dict[str, Any] = analysis_result["package_relations"]
    for package_name, package_info in package_relations.items():
        original_files = package_info.get("files", [])
        remaining = [f for f in original_files if f not in changed_file_strs]
        if remaining:
            updated_package_info = package_info.copy()
            updated_package_info["files"] = remaining
            updated_result["package_relations"][package_name] = updated_package_info

    references: list[Node] = analysis_result["references"]
    for ref in references:
        if ref.file_path not in changed_file_strs:
            updated_result["references"].append(ref)

    source_files: list[Path] = analysis_result["source_files"]
    for file_path in source_files:
        if str(file_path) not in changed_file_strs:
            updated_result["source_files"].append(file_path)

    _validate_no_dangling_references(updated_result)

    logger.info(
        f"Invalidated {len(changed_files)} files: kept {len(kept_nodes)} nodes, "
        f"{kept_edges} edges, {len(updated_result['references'])} references"
    )
    return updated_result


def merge_results(cached_result: dict[str, Any], new_result: dict[str, Any]) -> dict[str, Any]:
    """Union ``cached_result`` (post-invalidation) with ``new_result`` (fresh re-LSP).

    For overlapping keys (same file appearing in both), the new result wins
    for class hierarchies, packages, references, and diagnostics. Call-graph
    nodes from both sides merge; edges from either side that reference
    nodes present in the merged graph are kept.
    """
    merged_result: dict[str, Any] = {
        "call_graph": CallGraph(),
        "class_hierarchies": {},
        "package_relations": {},
        "references": [],
        "source_files": [],
    }

    for node in cached_result["call_graph"].nodes.values():
        merged_result["call_graph"].add_node(node)
    for node in new_result["call_graph"].nodes.values():
        merged_result["call_graph"].add_node(node)

    for edge in cached_result["call_graph"].edges:
        try:
            merged_result["call_graph"].add_edge(edge.get_source(), edge.get_destination())
        except ValueError:
            pass
    for edge in new_result["call_graph"].edges:
        try:
            merged_result["call_graph"].add_edge(edge.get_source(), edge.get_destination())
        except ValueError:
            pass

    merged_result["class_hierarchies"].update(cached_result["class_hierarchies"])
    merged_result["class_hierarchies"].update(new_result["class_hierarchies"])

    merged_result["package_relations"].update(cached_result["package_relations"])
    merged_result["package_relations"].update(new_result["package_relations"])

    new_source_files: list[Path] = new_result.get("source_files", [])
    new_file_paths = {str(path) for path in new_source_files}

    for ref in cached_result["references"]:
        if ref.file_path not in new_file_paths:
            merged_result["references"].append(ref)
    merged_result["references"].extend(new_result["references"])

    for file_path in cached_result["source_files"]:
        if str(file_path) not in new_file_paths:
            merged_result["source_files"].append(file_path)
    merged_result["source_files"].extend(new_source_files)

    cached_diagnostics: FileDiagnosticsMap = cached_result.get("diagnostics", {})
    new_diagnostics: FileDiagnosticsMap = new_result.get("diagnostics", {})
    merged_diagnostics: FileDiagnosticsMap = {
        fp: diags for fp, diags in cached_diagnostics.items() if fp not in new_file_paths
    }
    merged_diagnostics.update(new_diagnostics)
    if merged_diagnostics:
        merged_result["diagnostics"] = merged_diagnostics

    return merged_result


def _validate_no_dangling_references(analysis_result: dict[str, Any]) -> None:
    """Sanity-check: every edge reaches existing nodes, every reference / class /
    package points at a file in ``source_files``. Raises on violations."""
    call_graph: CallGraph = analysis_result["call_graph"]
    existing_nodes = set(call_graph.nodes.keys())
    source_file_strs = {str(path) for path in analysis_result["source_files"]}
    errors: list[str] = []

    for edge in call_graph.edges:
        src_name = edge.get_source()
        dst_name = edge.get_destination()
        if src_name not in existing_nodes:
            errors.append(f"Edge source '{src_name}' references non-existent node")
        if dst_name not in existing_nodes:
            errors.append(f"Edge destination '{dst_name}' references non-existent node")

    for ref in analysis_result["references"]:
        if ref.file_path not in source_file_strs:
            errors.append(f"Reference '{ref.fully_qualified_name}' from '{ref.file_path}' references non-existent file")

    for class_name, class_info in analysis_result["class_hierarchies"].items():
        class_file_path = class_info.get("file_path", "")
        if class_file_path and class_file_path not in source_file_strs:
            errors.append(f"Class hierarchy '{class_name}' references non-existent file '{class_file_path}'")

    for package_name, package_info in analysis_result["package_relations"].items():
        for package_file in package_info.get("files", []):
            if package_file not in source_file_strs:
                errors.append(f"Package '{package_name}' references non-existent file '{package_file}'")

    if errors:
        msg = "Dangling references after file invalidation:\n" + "\n".join(f"  - {e}" for e in errors)
        logger.error(msg)
        raise ValueError(msg)
