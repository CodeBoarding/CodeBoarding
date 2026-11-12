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
    _compute_depth_level,
    build_unified_analysis_json,
    parse_unified_analysis,
    read_metadata,
)
from diagram_analysis.exceptions import UnreadableAnalysisError
from infra.utils import ANALYSIS_FILENAME, FINGERPRINT_FILENAME

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

    def read(self) -> tuple[AnalysisInsights, dict[str, AnalysisInsights], dict] | None:
        """``(root_analysis, sub_analyses, raw_data)`` from ``analysis.json``, or ``None`` only when it is absent.

        Raises ``UnreadableAnalysisError`` when the file is there but this Core cannot read it.
        """
        data = self.read_document()
        if data is None:
            return None
        root_analysis, sub_analyses = parse_unified_analysis(data)
        return root_analysis, sub_analyses, data

    def read_document(self) -> dict | None:
        """The decoded ``analysis.json`` in whichever format wrote it; ``None`` only when it is absent."""
        with self._lock:
            if not self._analysis_path.exists():
                return None
            raw = self._analysis_path.read_bytes()
        try:
            return json.loads(raw)
        except ValueError as error:
            raise UnreadableAnalysisError(
                f"{self._analysis_path} is not valid JSON; run a full analysis to rewrite it"
            ) from error

    def write(
        self,
        analysis: AnalysisInsights,
        repo_dir: Path,
        source_tree_hash: str,
        *,
        sub_analyses: dict[str, AnalysisInsights],
        repo_name: str,
        file_coverage_summary: FileCoverageSummary,
        depth_cap: int,
        tree_spec: dict,
        expandable_component_ids: list[str] | None = None,
        sub_expandable_ids: dict[str, list[str]] | None = None,
    ) -> Path:
        """Write the analysis to ``analysis.json``; every field comes from the caller, none from the old file."""
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

        component_lookup = index_components_by_id(analysis, sub_analyses)
        sub_analyses_tuples: dict[str, tuple[AnalysisInsights, list[Component]]] = {}
        for cid, sub in sub_analyses.items():
            if sub_expandable_ids is not None and cid in sub_expandable_ids:
                # Authoritative per-sub set from the run's separability gate; same
                # rationale as the root list above (don't re-mark suppressed leaves).
                ids = set(sub_expandable_ids[cid])
                sub_expandable = [c for c in sub.components if c.component_id in ids]
            else:
                parent_component = component_lookup.get(cid)
                parent_had_clusters = bool(parent_component.source_cluster_ids) if parent_component else True
                sub_expandable = self._compute_expandable_components(sub, parent_had_clusters=parent_had_clusters)
            sub_analyses_tuples[cid] = (sub, sub_expandable)

        # A cap below the tree's own realized depth would silently freeze future
        # incremental/partial runs at that shallower depth (e.g. a partial run
        # against a depth-1 baseline that grafts on a new depth-2 subtree must not
        # save depth_cap=1 — the realized tree already exceeds it).
        depth_cap = max(depth_cap, _compute_depth_level(sub_analyses_tuples))

        payload = build_unified_analysis_json(
            analysis=analysis,
            expandable_components=expandable,
            repo_name=repo_name,
            repo_dir=repo_dir,
            source_tree_hash=source_tree_hash,
            depth_cap=depth_cap,
            sub_analyses=sub_analyses_tuples,
            file_coverage_summary=file_coverage_summary,
            tree_spec=tree_spec,
        )
        # Atomic write: build the JSON in a sibling temp file, then rename
        # over the destination.  A crashed process leaves either the prior
        # complete file or no file at all — never a half-written one.
        with self._lock:
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
# Public analysis I/O functions
# ---------------------------------------------------------------------------


def analysis_exists(output_dir: Path) -> bool:
    """True when ``analysis.json`` is present; raises ``UnreadableAnalysisError`` when this Core cannot read it."""
    if not (output_dir / ANALYSIS_FILENAME).is_file():
        return False
    return _get_store(output_dir).read() is not None


def load_full_analysis(output_dir: Path) -> tuple[AnalysisInsights, dict[str, AnalysisInsights]] | None:
    """Load both the root analysis and all sub-analyses from the unified analysis.json file.

    Returns ``(root_analysis, sub_analyses)`` or ``None`` if the file does not exist, and raises
    ``UnreadableAnalysisError`` when this Core cannot read it.
    Sub-analyses maps component_id to its nested AnalysisInsights, covering all depth levels.
    """
    result = _get_store(output_dir).read()
    if result is None:
        return None
    root_analysis, sub_analyses, _ = result
    return root_analysis, sub_analyses


def load_expandable_component_ids(output_dir: Path) -> set[str]:
    """Load component IDs currently persisted with ``can_expand=true``, without parsing the analysis."""
    document = _get_store(output_dir).read_document()
    if document is None:
        return set()

    expandable_ids: set[str] = set()

    def visit(components: list[dict]) -> None:
        for component in components:
            component_id = component.get("component_id")
            if component_id and component.get("can_expand") is True:
                expandable_ids.add(str(component_id))
            nested = component.get("components")
            if isinstance(nested, list):
                visit(nested)

    visit(document.get("components", []))
    return expandable_ids


def load_analysis_metadata(output_dir: Path) -> dict | None:
    """The raw metadata of ``analysis.json`` in any format, or ``None`` when there is no file.

    Why: the block is the same in every format, so a baseline this Core cannot parse still gives its depth cap.
    """
    document = _get_store(output_dir).read_document()
    return None if document is None else read_metadata(document)


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


def snapshot_analysis(output_dir: Path) -> str | None:
    """The current ``analysis.json`` text, or ``None`` when there is none."""
    path = output_dir / ANALYSIS_FILENAME
    return path.read_text(encoding="utf-8") if path.is_file() else None


def restore_analysis(output_dir: Path, snapshot: str | None) -> None:
    """Put back what :func:`snapshot_analysis` returned, removing the file when there was none."""
    path = output_dir / ANALYSIS_FILENAME
    with _get_store(output_dir)._lock:
        if snapshot is None:
            path.unlink(missing_ok=True)
        else:
            write_text_atomic(path, snapshot)


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
    *,
    sub_analyses: dict[str, AnalysisInsights],
    repo_name: str,
    file_coverage_summary: FileCoverageSummary,
    depth_cap: int,
    tree_spec: dict,
    expandable_component_ids: list[str] | None = None,
    sub_expandable_ids: dict[str, list[str]] | None = None,
) -> Path:
    """Save the analysis to a unified analysis.json file with file locking.

    ``repo_dir`` relativizes paths; ``source_tree_hash`` is the precomputed
    whole-tree version key (reproducible by consumers that fingerprint the tree).
    ``depth_cap`` is the run's configured depth ceiling.
    """
    return _get_store(output_dir).write(
        analysis,
        repo_dir,
        source_tree_hash,
        sub_analyses=sub_analyses,
        repo_name=repo_name,
        file_coverage_summary=file_coverage_summary,
        depth_cap=depth_cap,
        tree_spec=tree_spec,
        expandable_component_ids=expandable_component_ids,
        sub_expandable_ids=sub_expandable_ids,
    )
