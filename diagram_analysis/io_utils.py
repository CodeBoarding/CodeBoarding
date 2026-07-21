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
from utils import ANALYSIS_FILENAME, CODEBOARDING_DIR_NAME, FINGERPRINT_FILENAME, RUN_OUTPUT_DIR_NAME

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
        # 30s rather than 10s: cold-start LSP runs on Windows under AV scans
        # routinely steal multi-second cycles from contended writers.
        self._lock = FileLock(output_dir / f"{ANALYSIS_FILENAME}.lock", timeout=30)

    def _repo_dir_for_source_lookup(self) -> Path:
        """Recover the repo root from the output_dir layout — the source root a
        sub-analysis write hashes when the caller doesn't pass its own repo_dir."""
        if self._output_dir.name == RUN_OUTPUT_DIR_NAME and self._output_dir.parent.name == CODEBOARDING_DIR_NAME:
            return self._output_dir.parent.parent
        if self._output_dir.name == CODEBOARDING_DIR_NAME:
            return self._output_dir.parent
        return self._output_dir

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
        repo_dir: Path,
        source_tree_hash: str,
        expandable_component_ids: list[str] | None = None,
        sub_analyses: dict[str, AnalysisInsights] | None = None,
        repo_name: str = "",
        file_coverage_summary: FileCoverageSummary | None = None,
    ) -> Path:
        """Write the full analysis to ``analysis.json`` with file locking.

        If *sub_analyses* is not provided, existing sub-analyses on disk are
        preserved.
        """
        with self._lock:
            return self._write_with_lock_held(
                analysis,
                repo_dir,
                source_tree_hash,
                expandable_component_ids,
                sub_analyses,
                repo_name,
                file_coverage_summary,
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

            # Carry forward repo_name + source_tree_hash from the on-disk metadata:
            # a sub-analysis write doesn't recompute source state, so it preserves
            # the whole-tree hash the root write already stamped.
            metadata = raw_data.get("metadata", {})
            repo_name = metadata.get("repo_name", "")
            source_tree_hash = metadata.get("source_tree_hash", "")

            # Determine which root components are expandable
            all_expandable_ids = expandable_component_ids or list(sub_analyses.keys())

            return self._write_with_lock_held(
                root_analysis,
                self._repo_dir_for_source_lookup(),
                source_tree_hash,
                all_expandable_ids,
                sub_analyses,
                repo_name,
            )

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
        repo_dir: Path,
        source_tree_hash: str,
        expandable_component_ids: list[str] | None = None,
        sub_analyses: dict[str, AnalysisInsights] | None = None,
        repo_name: str = "",
        file_coverage_summary: FileCoverageSummary | None = None,
    ) -> Path:
        """Write ``analysis.json`` — caller must already hold ``self._lock``."""
        # A caller-provided set is authoritative: it already reflects the run's expansion
        # decision (including the separability gate that keeps cohesive components as leaves),
        # so unioning in the unconditional ``should_expand_component`` set would re-mark those
        # suppressed components expandable. Only fall back to the deterministic computation when
        # the caller supplied nothing.
        if expandable_component_ids is not None:
            expandable_ids = set(expandable_component_ids)
        else:
            expandable_ids = {
                c.component_id for c in self._compute_expandable_components(analysis, parent_had_clusters=True)
            }
        expandable = [c for c in analysis.components if c.component_id in expandable_ids]

        # Preserve existing metadata fields from disk when not explicitly provided
        if sub_analyses is None or file_coverage_summary is None or not repo_name:
            existing = self.read()
            if existing:
                _, existing_subs, existing_data = existing
                if sub_analyses is None:
                    sub_analyses = existing_subs
                metadata = existing_data.get("metadata", {})
                if not repo_name:
                    repo_name = metadata.get("repo_name", "")
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
            repo_dir=repo_dir,
            source_tree_hash=source_tree_hash,
            sub_analyses=sub_analyses_tuples,
            file_coverage_summary=file_coverage_summary,
        )
        write_text_atomic(self._analysis_path, payload)
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
    """True when output_dir holds a parseable ``analysis.json``."""
    if not (output_dir / ANALYSIS_FILENAME).is_file():
        return False
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


def write_text_atomic(path: Path, text: str) -> None:
    """Write *text* to *path* via a temp file + ``os.replace`` so a crash mid-write
    can't leave a truncated file. Creates parent dirs."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp, path)
    except Exception:
        Path(tmp).unlink(missing_ok=True)
        raise


# Whole-tree fingerprint sidecar. analysis.json's ``files`` block covers only
# component-assigned files; the sidecar covers the whole analyzable tree, so the
# incremental diff also sees changes to docs/configs/unclustered source.
def write_fingerprint(output_dir: Path, file_hashes: dict[str, str]) -> None:
    """Persist the whole-tree fingerprint next to ``analysis.json``. Best-effort."""
    try:
        write_text_atomic(Path(output_dir) / FINGERPRINT_FILENAME, json.dumps({"files": file_hashes}, indent=2))
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
    repo_dir: Path,
    source_tree_hash: str,
    expandable_component_ids: list[str] | None = None,
    sub_analyses: dict[str, AnalysisInsights] | None = None,
    repo_name: str = "",
    file_coverage_summary: FileCoverageSummary | None = None,
) -> Path:
    """Save the analysis to a unified analysis.json file with file locking.

    ``repo_dir`` relativizes paths; ``source_tree_hash`` is the precomputed
    whole-tree version key (reproducible by consumers that fingerprint the tree).
    """
    return _get_store(output_dir).write(
        analysis,
        repo_dir,
        source_tree_hash,
        expandable_component_ids,
        sub_analyses,
        repo_name,
        file_coverage_summary,
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
