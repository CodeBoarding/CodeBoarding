"""
I/O utilities for incremental analysis.

This module provides coordinated read/write access to the unified
``analysis.json`` file through **free functions** (``save_analysis``,
``load_analysis``, etc.).

Internally a singleton ``_AnalysisFileStore`` per ``output_dir`` owns the
``FileLock`` and in-memory cache.  External code should **never** instantiate
``_AnalysisFileStore`` directly – always use the free functions which route
through the module-level registry.

The unified format stores all analysis data (root + sub-analyses) in a single
analysis.json file with nested components.
"""

import json
import logging
import os
import tempfile
from pathlib import Path

from filelock import FileLock

from agents.agent_responses import AnalysisInsights, Component, index_components_by_id
from agents.planner_agent import should_expand_component
from diagram_analysis.analysis_json import (
    FileCoverageSummary,
    build_unified_analysis_json,
    parse_unified_analysis,
)
from repo_utils.path_utils import normalize_repo_path
from utils import ANALYSIS_FILENAME

logger = logging.getLogger(__name__)


class _AnalysisFileStore:
    """Coordinated reader/writer for ``analysis.json`` with file locking.

    All concurrent access to a given ``analysis.json`` should go through the
    same ``_AnalysisFileStore`` instance (or the module-level free functions
    which share instances via ``_get_store``).  The store owns the
    ``FileLock`` that serialises writes across threads and processes.
    """

    @staticmethod
    def _compute_expandable_components(
        analysis: AnalysisInsights,
        parent_had_clusters: bool,
    ) -> list[Component]:
        """Compute expandable components deterministically for one analysis level."""
        return [c for c in analysis.components if should_expand_component(c, parent_had_clusters=parent_had_clusters)]

    def __init__(self, output_dir: Path) -> None:
        output_dir.mkdir(parents=True, exist_ok=True)
        self._output_dir = output_dir
        self._analysis_path = output_dir / ANALYSIS_FILENAME
        # Last repo_dir written, reused by write_sub so sub-analysis saves keep
        # the same whole-tree source_tree_hash the root write computed.
        self._repo_dir: Path | None = None
        # 30s rather than 10s: cold-start LSP runs on Windows under AV scans
        # routinely steal multi-second cycles from contended writers.
        self._lock = FileLock(output_dir / f"{ANALYSIS_FILENAME}.lock", timeout=30)

    def read(self) -> tuple[AnalysisInsights, dict[str, AnalysisInsights], dict] | None:
        """Load the unified ``analysis.json`` from disk.

        Returns ``(root_analysis, sub_analyses_dict, raw_data)`` or ``None``
        if the file does not exist or cannot be parsed.
        """
        with self._lock:
            if not self._analysis_path.exists():
                return None

            try:
                with open(self._analysis_path, "r", encoding="utf-8") as f:
                    data = json.load(f)

                root_analysis, sub_analyses = parse_unified_analysis(data)
                return (root_analysis, sub_analyses, data)
            except Exception as e:
                logger.error(f"Failed to load unified analysis: {e}")
                return None

    def read_root(self) -> AnalysisInsights | None:
        """Load just the root analysis."""
        result = self.read()
        return result[0] if result else None

    def exists(self) -> bool:
        """True when a parseable ``analysis.json`` is present on disk."""
        return self.read_root() is not None

    def read_sub(self, component_id: str) -> AnalysisInsights | None:
        """Load a sub-analysis for a specific component by component_id."""
        result = self.read()
        if result is None:
            return None

        _, sub_analyses, _ = result
        sub = sub_analyses.get(component_id)
        if sub is None:
            logger.debug(f"No sub-analysis found for component ID '{component_id}' in unified analysis")
        return sub

    def write(
        self,
        analysis: AnalysisInsights,
        expandable_component_ids: list[str] | None = None,
        sub_analyses: dict[str, AnalysisInsights] | None = None,
        repo_name: str = "",
        file_coverage_summary: FileCoverageSummary | None = None,
        commit_hash: str = "",
        repo_dir: Path | None = None,
    ) -> Path:
        """Write the full analysis to ``analysis.json`` with file locking.

        If *sub_analyses* is not provided, existing sub-analyses on disk are
        preserved.
        """
        with self._lock:
            return self._write_with_lock_held(
                analysis,
                expandable_component_ids,
                sub_analyses,
                repo_name,
                file_coverage_summary,
                commit_hash,
                repo_dir,
            )

    def write_sub(
        self,
        sub_analysis: AnalysisInsights,
        component_id: str,
        expandable_component_ids: list[str] | None = None,
    ) -> Path:
        """Update a single sub-analysis within ``analysis.json``.

        Acquires the file lock, loads the existing unified file, replaces the
        sub-analysis for *component_id*, and re-writes the whole file.
        """
        with self._lock:
            existing = self.read()
            if existing is None:
                logger.error(f"Cannot save sub-analysis: no existing analysis.json in {self._output_dir}")
                return self._analysis_path

            root_analysis, sub_analyses, raw_data = existing

            # Update the sub-analysis for this component
            sub_analyses[component_id] = sub_analysis

            # Determine repo_name from existing metadata
            repo_name = ""
            if "metadata" in raw_data:
                repo_name = raw_data["metadata"].get("repo_name", "")

            # Determine which root components are expandable
            all_expandable_ids = expandable_component_ids or list(sub_analyses.keys())

            return self._write_with_lock_held(root_analysis, all_expandable_ids, sub_analyses, repo_name)

    def _read_existing_source_tree_hash(self) -> str:
        """The ``metadata.source_tree_hash`` currently on disk, or '' if absent."""
        try:
            with open(self._analysis_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            return ""
        metadata = data.get("metadata", {}) if isinstance(data, dict) else {}
        value = metadata.get("source_tree_hash", "")
        return value if isinstance(value, str) else ""

    def detect_expanded_components(self, analysis: AnalysisInsights) -> list[str]:
        """Find component IDs that have sub-analyses in the unified ``analysis.json``."""
        result = self.read()
        if result is None:
            return []

        _, sub_analyses, _ = result
        return [c.component_id for c in analysis.components if c.component_id in sub_analyses]

    def _write_with_lock_held(
        self,
        analysis: AnalysisInsights,
        expandable_component_ids: list[str] | None = None,
        sub_analyses: dict[str, AnalysisInsights] | None = None,
        repo_name: str = "",
        file_coverage_summary: FileCoverageSummary | None = None,
        commit_hash: str = "",
        repo_dir: Path | None = None,
    ) -> Path:
        """Write ``analysis.json`` — caller must already hold ``self._lock``."""
        if repo_dir is not None:
            self._repo_dir = repo_dir
        else:
            repo_dir = self._repo_dir
        # If no repo_dir is available (e.g. a bare sub-analysis write), preserve the
        # existing whole-tree source_tree_hash from disk rather than let the JSON
        # builder downgrade it to a component-only hash.
        source_tree_hash_override = ""
        if repo_dir is None:
            source_tree_hash_override = self._read_existing_source_tree_hash()
        # Keep caller-provided expandables, but also preserve deterministic planner eligibility.
        expandable_ids = set(expandable_component_ids or [])
        expandable_ids.update(
            c.component_id for c in self._compute_expandable_components(analysis, parent_had_clusters=True)
        )
        expandable = [c for c in analysis.components if c.component_id in expandable_ids]

        # Preserve existing metadata fields from disk when not explicitly provided
        if sub_analyses is None or file_coverage_summary is None or not repo_name or not commit_hash:
            existing = self.read()
            if existing:
                _, existing_subs, existing_data = existing
                if sub_analyses is None:
                    sub_analyses = existing_subs
                metadata = existing_data.get("metadata", {})
                if not repo_name:
                    repo_name = metadata.get("repo_name", "")
                if not commit_hash:
                    commit_hash = metadata.get("commit_hash", "")
                if file_coverage_summary is None:
                    raw_summary = metadata.get("file_coverage_summary")
                    if raw_summary:
                        file_coverage_summary = FileCoverageSummary(
                            total_files=raw_summary.get("total_files", 0),
                            analyzed=raw_summary.get("analyzed", 0),
                            not_analyzed=raw_summary.get("not_analyzed", 0),
                            not_analyzed_by_reason=raw_summary.get("not_analyzed_by_reason", {}),
                        )

        # Convert sub_analyses dict to the format expected by build_unified_analysis_json
        sub_analyses_tuples: dict[str, tuple[AnalysisInsights, list[Component]]] | None = None
        if sub_analyses:
            component_lookup = index_components_by_id(analysis, sub_analyses)
            sub_analyses_tuples = {}
            for cid, sub in sub_analyses.items():
                parent_component = component_lookup.get(cid)
                parent_had_clusters = bool(parent_component.source_cluster_ids) if parent_component else True
                sub_expandable = self._compute_expandable_components(sub, parent_had_clusters=parent_had_clusters)
                sub_analyses_tuples[cid] = (sub, sub_expandable)

        # Atomic write: build the JSON in a sibling temp file, then rename
        # over the destination.  A crashed process leaves either the prior
        # complete file or no file at all — never a half-written one.
        payload = build_unified_analysis_json(
            analysis=analysis,
            expandable_components=expandable,
            repo_name=repo_name,
            sub_analyses=sub_analyses_tuples,
            file_coverage_summary=file_coverage_summary,
            commit_hash=commit_hash,
            repo_dir=repo_dir,
            source_tree_hash_override=source_tree_hash_override,
        )
        tmp_fd, tmp_name = tempfile.mkstemp(
            prefix=f".{self._analysis_path.name}.",
            dir=str(self._analysis_path.parent),
        )
        try:
            with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
                f.write(payload)
            os.replace(tmp_name, self._analysis_path)
        except Exception:
            Path(tmp_name).unlink(missing_ok=True)
            raise

        return self._analysis_path


# ---------------------------------------------------------------------------
# Module-level store registry (one store per output_dir)
# ---------------------------------------------------------------------------

_stores: dict[str, _AnalysisFileStore] = {}


def _get_store(output_dir: Path) -> _AnalysisFileStore:
    """Return the shared ``_AnalysisFileStore`` for *output_dir*."""
    key = str(output_dir.resolve())
    if key not in _stores:
        _stores[key] = _AnalysisFileStore(output_dir)
    return _stores[key]


# ---------------------------------------------------------------------------
# Free-function wrappers (preserve the original public API)
# ---------------------------------------------------------------------------


def load_root_analysis(output_dir: Path) -> AnalysisInsights | None:
    """Load the root analysis from the unified analysis.json file."""
    return _get_store(output_dir).read_root()


def analysis_exists(output_dir: Path) -> bool:
    """True when *output_dir* holds a parseable ``analysis.json``."""
    return _get_store(output_dir).exists()


def load_full_analysis(output_dir: Path) -> tuple[AnalysisInsights, dict[str, AnalysisInsights]] | None:
    """Load both the root analysis and all sub-analyses from the unified analysis.json file.

    Returns ``(root_analysis, sub_analyses)`` or ``None`` if the file does not exist.
    Sub-analyses maps component_id to its nested AnalysisInsights, covering all depth levels.
    """
    result = _get_store(output_dir).read()
    if result is None:
        return None
    root_analysis, sub_analyses, _ = result
    return root_analysis, sub_analyses


def load_analysis_metadata(output_dir: Path) -> dict | None:
    """Load raw metadata from the unified analysis file."""
    result = _get_store(output_dir).read()
    if result is None:
        return None
    return result[2].get("metadata")


def load_analysis_commit_hash(output_dir: Path) -> str | None:
    """Return ``metadata.commit_hash`` from live ``analysis.json``.

    This is the user's git commit at which the analysis was generated, and
    is the right standalone-CLI default for an incremental ``--base-ref``:
    the CLI should answer "what changed in the user's git history relative
    to the user's last full analysis?", not "what changed relative to the
    wrapper's snapshot commit?" (which is meaningful only inside the
    extension/snapshot-worktree flow).

    Returns ``None`` when the file is absent, unreadable, malformed, or
    the field is missing/empty. Reads raw JSON so a partially-malformed
    analysis.json still yields a usable baseline pointer.
    """
    path = Path(output_dir) / ANALYSIS_FILENAME
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    metadata = data.get("metadata")
    if not isinstance(metadata, dict):
        return None
    sha = metadata.get("commit_hash")
    return sha if isinstance(sha, str) and sha else None


# Whole-tree fingerprint sidecar. analysis.json's ``files`` block covers only
# component-assigned files; the sidecar covers the whole analyzable tree, so the
# incremental diff also sees changes to docs/configs/unclustered source.
FINGERPRINT_FILENAME = "fingerprint.json"


def write_fingerprint(output_dir: Path, file_hashes: dict[str, str]) -> None:
    """Persist the whole-tree fingerprint next to ``analysis.json``. Best-effort."""
    try:
        (Path(output_dir) / FINGERPRINT_FILENAME).write_text(
            json.dumps({"files": file_hashes}, indent=2), encoding="utf-8"
        )
    except OSError as exc:
        logger.warning("Failed to write fingerprint sidecar (continuing): %s", exc)


def read_fingerprint(output_dir: Path) -> dict[str, str] | None:
    """Read the whole-tree fingerprint sidecar. ``None`` when absent/unreadable."""
    try:
        data = json.loads((Path(output_dir) / FINGERPRINT_FILENAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    files = data.get("files") if isinstance(data, dict) else None
    if not isinstance(files, dict):
        return None
    return {str(k): str(v) for k, v in files.items() if isinstance(v, str) and v}


def save_analysis(
    analysis: AnalysisInsights,
    output_dir: Path,
    expandable_component_ids: list[str] | None = None,
    sub_analyses: dict[str, AnalysisInsights] | None = None,
    repo_name: str = "",
    file_coverage_summary: FileCoverageSummary | None = None,
    commit_hash: str = "",
    repo_dir: Path | None = None,
) -> Path:
    """Save the analysis to a unified analysis.json file with file locking.

    Pass ``repo_dir`` so ``source_tree_hash`` covers the whole analyzable tree
    (reproducible by consumers that fingerprint the working tree).
    """
    return _get_store(output_dir).write(
        analysis,
        expandable_component_ids,
        sub_analyses,
        repo_name,
        file_coverage_summary,
        commit_hash,
        repo_dir,
    )


def load_sub_analysis(output_dir: Path, component_id: str) -> AnalysisInsights | None:
    """Load a sub-analysis for a component from the unified analysis.json."""
    return _get_store(output_dir).read_sub(component_id)


def save_sub_analysis(
    sub_analysis: AnalysisInsights,
    output_dir: Path,
    component_id: str,
    expandable_component_ids: list[str] | None = None,
) -> Path:
    """Save/update a sub-analysis for a component in the unified analysis.json."""
    return _get_store(output_dir).write_sub(sub_analysis, component_id, expandable_component_ids)
