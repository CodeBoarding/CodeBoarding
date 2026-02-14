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
from pathlib import Path

from filelock import FileLock

from agents.agent_responses import AnalysisInsights, Component
from agents.planner_agent import should_expand_component
from diagram_analysis.analysis_json import (
    FileCoverageSummary,
    build_unified_analysis_json,
    parse_unified_analysis,
)

logger = logging.getLogger(__name__)


def _compute_expandable_components(
    analysis: AnalysisInsights,
    parent_had_clusters: bool,
) -> list[Component]:
    """Compute expandable components deterministically for one analysis level."""
    return [c for c in analysis.components if should_expand_component(c, parent_had_clusters=parent_had_clusters)]


def _build_component_lookup(
    root_analysis: AnalysisInsights,
    sub_analyses: dict[str, AnalysisInsights],
) -> dict[str, Component]:
    """Build component_id -> component lookup across root and sub-analyses."""
    lookup: dict[str, Component] = {}
    for component in root_analysis.components:
        lookup[component.component_id] = component
    for sub_analysis in sub_analyses.values():
        for component in sub_analysis.components:
            lookup[component.component_id] = component
    return lookup


class _AnalysisFileStore:
    """Coordinated reader/writer for ``analysis.json`` with file locking.

    All concurrent access to a given ``analysis.json`` should go through the
    same ``_AnalysisFileStore`` instance (or the module-level free functions
    which share instances via ``_get_store``).  The store owns the
    ``FileLock`` that serialises writes across threads and processes.
    """

    def __init__(self, output_dir: Path) -> None:
        output_dir.mkdir(parents=True, exist_ok=True)
        self._output_dir = output_dir
        self._analysis_path = output_dir / "analysis.json"
        self._lock = FileLock(output_dir / "analysis.json.lock", timeout=10)

    def read(self) -> tuple[AnalysisInsights, dict[str, AnalysisInsights], dict] | None:
        """Load the unified ``analysis.json`` from disk.

        Returns ``(root_analysis, sub_analyses_dict, raw_data)`` or ``None``
        if the file does not exist or cannot be parsed.
        """
        with self._lock:
            if not self._analysis_path.exists():
                return None

            try:
                with open(self._analysis_path, "r") as f:
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
        expandable_components: list[str] | None = None,
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
                analysis, expandable_components, sub_analyses, repo_name, file_coverage_summary
            )

    def write_sub(
        self,
        sub_analysis: AnalysisInsights,
        component_id: str,
        expandable_components: list[str] | None = None,
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
            all_expandable = expandable_components or list(sub_analyses.keys())

            return self._write_with_lock_held(root_analysis, all_expandable, sub_analyses, repo_name)

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
        expandable_components: list[str] | None = None,
        sub_analyses: dict[str, AnalysisInsights] | None = None,
        repo_name: str = "",
        file_coverage_summary: FileCoverageSummary | None = None,
    ) -> Path:
        """Write ``analysis.json`` — caller must already hold ``self._lock``."""
        # Keep caller-provided root expandables, but also preserve deterministic planner eligibility.
        root_expandable_ids = set(expandable_components or [])
        root_expandable_ids.update(
            c.component_id for c in _compute_expandable_components(analysis, parent_had_clusters=True)
        )
        expandable = [c for c in analysis.components if c.component_id in root_expandable_ids]

        # If no sub_analyses provided, try to preserve existing ones from disk
        if sub_analyses is None:
            existing = self.read()
            if existing:
                _, existing_subs, existing_data = existing
                sub_analyses = existing_subs
                if not repo_name and "metadata" in existing_data:
                    repo_name = existing_data["metadata"].get("repo_name", "")

        # Convert sub_analyses dict to the format expected by build_unified_analysis_json
        sub_analyses_tuples: dict[str, tuple[AnalysisInsights, list[Component]]] | None = None
        if sub_analyses:
            component_lookup = _build_component_lookup(analysis, sub_analyses)
            sub_analyses_tuples = {}
            for cid, sub in sub_analyses.items():
                parent_component = component_lookup.get(cid)
                parent_had_clusters = bool(parent_component.source_cluster_ids) if parent_component else True
                sub_expandable = _compute_expandable_components(sub, parent_had_clusters=parent_had_clusters)
                sub_analyses_tuples[cid] = (sub, sub_expandable)

        with open(self._analysis_path, "w") as f:
            f.write(
                build_unified_analysis_json(
                    analysis=analysis,
                    expandable_components=expandable,
                    repo_name=repo_name,
                    sub_analyses=sub_analyses_tuples,
                    file_coverage_summary=file_coverage_summary,
                )
            )

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


def load_analysis(output_dir: Path) -> AnalysisInsights | None:
    """Load the root analysis from the unified analysis.json file."""
    return _get_store(output_dir).read_root()


def save_analysis(
    analysis: AnalysisInsights,
    output_dir: Path,
    expandable_components: list[str] | None = None,
    sub_analyses: dict[str, AnalysisInsights] | None = None,
    repo_name: str = "",
    file_coverage_summary: FileCoverageSummary | None = None,
) -> Path:
    """Save the analysis to a unified analysis.json file with file locking."""
    return _get_store(output_dir).write(analysis, expandable_components, sub_analyses, repo_name, file_coverage_summary)


def load_sub_analysis(output_dir: Path, component_id: str) -> AnalysisInsights | None:
    """Load a sub-analysis for a component from the unified analysis.json."""
    return _get_store(output_dir).read_sub(component_id)


def save_sub_analysis(
    sub_analysis: AnalysisInsights,
    output_dir: Path,
    component_id: str,
    expandable_components: list[str] | None = None,
) -> Path:
    """Save/update a sub-analysis for a component in the unified analysis.json."""
    return _get_store(output_dir).write_sub(sub_analysis, component_id, expandable_components)
