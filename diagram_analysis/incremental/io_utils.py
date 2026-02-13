"""
I/O utilities for incremental analysis.

This module provides the ``AnalysisFileStore`` class – a coordinated
reader/writer for the unified ``analysis.json`` file – along with thin
free-function wrappers that preserve the original public API.

The unified format stores all analysis data (root + sub-analyses) in a single
analysis.json file with nested components.
"""

import json
import logging
from pathlib import Path

from filelock import FileLock

from agents.agent_responses import AnalysisInsights, Component
from diagram_analysis.analysis_json import (
    build_unified_analysis_json,
    parse_unified_analysis,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# AnalysisFileStore – single point of coordination for analysis.json
# ---------------------------------------------------------------------------


class AnalysisFileStore:
    """Coordinated reader/writer for ``analysis.json`` with file locking and caching.

    All concurrent access to a given ``analysis.json`` should go through the
    same ``AnalysisFileStore`` instance (or the module-level free functions
    which share instances via ``_get_store``).  The store owns:

    * the ``FileLock`` that serialises writes,
    * an in-memory cache that is invalidated on every write.
    """

    def __init__(self, output_dir: Path) -> None:
        output_dir.mkdir(parents=True, exist_ok=True)
        self._output_dir = output_dir
        self._analysis_path = output_dir / "analysis.json"
        self._lock = FileLock(output_dir / "analysis.json.lock", timeout=120)
        self._cache: tuple[AnalysisInsights, dict[str, AnalysisInsights], dict] | None = None

    # -- public readers -----------------------------------------------------

    def read(self) -> tuple[AnalysisInsights, dict[str, AnalysisInsights], dict] | None:
        """Load and cache the unified ``analysis.json``.

        Returns ``(root_analysis, sub_analyses_dict, raw_data)`` or ``None``
        if the file does not exist or cannot be parsed.
        """
        with self._lock:
            if self._cache is not None:
                return self._cache

            if not self._analysis_path.exists():
                return None

            try:
                with open(self._analysis_path, "r") as f:
                    data = json.load(f)

                root_analysis, sub_analyses = parse_unified_analysis(data)
                result = (root_analysis, sub_analyses, data)
                self._cache = result
                return result
            except Exception as e:
                logger.error(f"Failed to load unified analysis: {e}")
                return None

    def read_root(self) -> AnalysisInsights | None:
        """Load just the root analysis."""
        with self._lock:
            result = self.read()
            return result[0] if result else None

    def read_sub(self, component_name: str) -> AnalysisInsights | None:
        """Load a sub-analysis for a specific component."""
        with self._lock:
            result = self.read()
            if result is None:
                return None

            _, sub_analyses, _ = result
            sub = sub_analyses.get(component_name)
            if sub is None:
                logger.debug(f"No sub-analysis found for component '{component_name}' in unified analysis")
            return sub

    # -- public writers -----------------------------------------------------

    def write(
        self,
        analysis: AnalysisInsights,
        expandable_components: list[str] | None = None,
        sub_analyses: dict[str, AnalysisInsights] | None = None,
        repo_name: str = "",
    ) -> Path:
        """Write the full analysis to ``analysis.json`` with file locking.

        If *sub_analyses* is not provided, existing sub-analyses on disk are
        preserved.
        """
        with self._lock:
            self._invalidate_cache()
            return self._write_unlocked(analysis, expandable_components, sub_analyses, repo_name)

    def write_sub(
        self,
        sub_analysis: AnalysisInsights,
        component_name: str,
        expandable_components: list[str] | None = None,
    ) -> Path:
        """Update a single sub-analysis within ``analysis.json``.

        Acquires the file lock, loads the existing unified file, replaces the
        sub-analysis for *component_name*, and re-writes the whole file.
        """
        with self._lock:
            self._invalidate_cache()

            existing = self.read()
            if existing is None:
                logger.error(f"Cannot save sub-analysis: no existing analysis.json in {self._output_dir}")
                return self._analysis_path

            root_analysis, sub_analyses, raw_data = existing

            # Update the sub-analysis for this component
            sub_analyses[component_name] = sub_analysis

            # Determine repo_name from existing metadata
            repo_name = ""
            if "metadata" in raw_data:
                repo_name = raw_data["metadata"].get("repo_name", "")

            # Determine which root components are expandable
            all_expandable = expandable_components or list(sub_analyses.keys())

            return self._write_unlocked(root_analysis, all_expandable, sub_analyses, repo_name)

    def write_raw(self, content: str) -> Path:
        """Write raw JSON content to ``analysis.json``.

        Acquires the file lock and invalidates the cache.  Used by callers
        that build the JSON payload themselves (e.g. the final write in
        ``DiagramGenerator.generate_analysis``).
        """
        with self._lock:
            self._invalidate_cache()
            with open(self._analysis_path, "w") as f:
                f.write(content)
            return self._analysis_path

    # -- public helpers -----------------------------------------------------

    def detect_expanded_components(self, analysis: AnalysisInsights) -> list[str]:
        """Find components that have sub-analyses in the unified ``analysis.json``."""
        result = self.read()
        if result is None:
            return []

        _, sub_analyses, _ = result
        return [c.name for c in analysis.components if c.name in sub_analyses]

    # -- internals ----------------------------------------------------------

    def _invalidate_cache(self) -> None:
        self._cache = None

    def _write_unlocked(
        self,
        analysis: AnalysisInsights,
        expandable_components: list[str] | None = None,
        sub_analyses: dict[str, AnalysisInsights] | None = None,
        repo_name: str = "",
    ) -> Path:
        """Write ``analysis.json`` — caller must already hold ``self._lock``."""
        # Build expandable component list
        expandable: list[Component] = []
        if expandable_components:
            expandable = [c for c in analysis.components if c.name in expandable_components]

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
            sub_analyses_tuples = {}
            for name, sub in sub_analyses.items():
                sub_expandable = [c for c in sub.components if c.name in sub_analyses]
                sub_analyses_tuples[name] = (sub, sub_expandable)

        with open(self._analysis_path, "w") as f:
            f.write(
                build_unified_analysis_json(
                    analysis=analysis,
                    expandable_components=expandable,
                    repo_name=repo_name,
                    sub_analyses=sub_analyses_tuples,
                )
            )

        self._invalidate_cache()
        return self._analysis_path


# ---------------------------------------------------------------------------
# Module-level store registry (one store per output_dir)
# ---------------------------------------------------------------------------

_stores: dict[str, AnalysisFileStore] = {}


def _get_store(output_dir: Path) -> AnalysisFileStore:
    """Return the shared ``AnalysisFileStore`` for *output_dir*."""
    key = str(output_dir.resolve())
    if key not in _stores:
        _stores[key] = AnalysisFileStore(output_dir)
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
) -> Path:
    """Save the analysis to a unified analysis.json file with file locking."""
    return _get_store(output_dir).write(analysis, expandable_components, sub_analyses, repo_name)


def load_sub_analysis(output_dir: Path, component_name: str) -> AnalysisInsights | None:
    """Load a sub-analysis for a component from the unified analysis.json."""
    return _get_store(output_dir).read_sub(component_name)


def save_sub_analysis(
    sub_analysis: AnalysisInsights,
    output_dir: Path,
    component_name: str,
    expandable_components: list[str] | None = None,
) -> Path:
    """Save/update a sub-analysis for a component in the unified analysis.json."""
    return _get_store(output_dir).write_sub(sub_analysis, component_name, expandable_components)
