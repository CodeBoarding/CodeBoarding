"""
I/O utilities for incremental analysis.

This module provides functions to load and save analysis files
and manifests during incremental updates.

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

# Module-level cache for loaded unified data
_unified_cache: dict[str, tuple[AnalysisInsights, dict[str, AnalysisInsights], dict]] = {}


def _cache_key(output_dir: Path) -> str:
    return str(output_dir.resolve())


def _invalidate_cache(output_dir: Path) -> None:
    key = _cache_key(output_dir)
    _unified_cache.pop(key, None)


def analysis_lock_path(output_dir: Path) -> Path:
    """Return the path to the advisory lock file for analysis.json."""
    return output_dir / "analysis.json.lock"


def _load_unified_data(output_dir: Path) -> tuple[AnalysisInsights, dict[str, AnalysisInsights], dict] | None:
    """Load and cache the unified analysis.json file.

    Returns:
        (root_analysis, sub_analyses_dict, raw_data) or None if file doesn't exist.
    """
    key = _cache_key(output_dir)
    if key in _unified_cache:
        return _unified_cache[key]

    analysis_path = output_dir / "analysis.json"
    if not analysis_path.exists():
        return None

    try:
        with open(analysis_path, "r") as f:
            data = json.load(f)

        root_analysis, sub_analyses = parse_unified_analysis(data)
        result = (root_analysis, sub_analyses, data)
        _unified_cache[key] = result
        return result
    except Exception as e:
        logger.error(f"Failed to load unified analysis: {e}")
        return None


def load_analysis(output_dir: Path) -> AnalysisInsights | None:
    """Load the root analysis from the unified analysis.json file."""
    result = _load_unified_data(output_dir)
    if result is None:
        return None
    return result[0]


def _save_analysis_unlocked(
    analysis: AnalysisInsights,
    output_dir: Path,
    expandable_components: list[str] | None = None,
    sub_analyses: dict[str, AnalysisInsights] | None = None,
    repo_name: str = "",
) -> Path:
    """Save the analysis to a unified analysis.json file. Caller must hold the file lock.

    If sub_analyses is not provided, attempts to preserve existing sub-analyses
    from the current file on disk.
    """
    analysis_path = output_dir / "analysis.json"

    # Build expandable component list
    expandable: list[Component] = []
    if expandable_components:
        expandable = [c for c in analysis.components if c.name in expandable_components]

    # If no sub_analyses provided, try to load existing ones from disk
    if sub_analyses is None:
        existing = _load_unified_data(output_dir)
        if existing:
            _, existing_subs, existing_data = existing
            sub_analyses = existing_subs
            # Preserve metadata from existing file
            if not repo_name and "metadata" in existing_data:
                repo_name = existing_data["metadata"].get("repo_name", "")

    # Convert sub_analyses dict to the format expected by build_unified_analysis_json
    sub_analyses_tuples: dict[str, tuple[AnalysisInsights, list[Component]]] | None = None
    if sub_analyses:
        sub_analyses_tuples = {}
        for name, sub in sub_analyses.items():
            # Determine which sub-components are expandable (they have their own sub-analyses)
            sub_expandable = [c for c in sub.components if c.name in sub_analyses]
            sub_analyses_tuples[name] = (sub, sub_expandable)

    with open(analysis_path, "w") as f:
        f.write(
            build_unified_analysis_json(
                analysis=analysis,
                expandable_components=expandable,
                repo_name=repo_name,
                sub_analyses=sub_analyses_tuples,
            )
        )

    _invalidate_cache(output_dir)
    return analysis_path


def save_analysis(
    analysis: AnalysisInsights,
    output_dir: Path,
    expandable_components: list[str] | None = None,
    sub_analyses: dict[str, AnalysisInsights] | None = None,
    repo_name: str = "",
    depth_level: int = 1,
) -> Path:
    """Save the analysis to a unified analysis.json file with file locking.

    If sub_analyses is not provided, attempts to preserve existing sub-analyses
    from the current file on disk.
    """
    lock = FileLock(analysis_lock_path(output_dir), timeout=120)
    with lock:
        _invalidate_cache(output_dir)
        return _save_analysis_unlocked(
            analysis, output_dir, expandable_components, sub_analyses, repo_name, depth_level
        )


def load_sub_analysis(output_dir: Path, component_name: str) -> AnalysisInsights | None:
    """Load a sub-analysis for a component from the unified analysis.json."""
    result = _load_unified_data(output_dir)
    if result is None:
        return None

    _, sub_analyses, _ = result
    sub = sub_analyses.get(component_name)
    if sub is None:
        logger.debug(f"No sub-analysis found for component '{component_name}' in unified analysis")
    return sub


def save_sub_analysis(
    sub_analysis: AnalysisInsights,
    output_dir: Path,
    component_name: str,
    expandable_components: list[str] | None = None,
) -> Path:
    """Save/update a sub-analysis for a component in the unified analysis.json.

    Acquires a file lock, loads the existing unified file, replaces the sub-analysis
    for the given component, and re-writes the whole file atomically.
    """
    lock = FileLock(analysis_lock_path(output_dir), timeout=120)
    with lock:
        _invalidate_cache(output_dir)

        existing = _load_unified_data(output_dir)
        if existing is None:
            logger.error(f"Cannot save sub-analysis: no existing analysis.json in {output_dir}")
            return output_dir / "analysis.json"

        root_analysis, sub_analyses, raw_data = existing

        # Update the sub-analysis for this component
        sub_analyses[component_name] = sub_analysis

        # Determine repo_name and depth_level from existing metadata
        repo_name = ""
        depth_level = 1
        if "metadata" in raw_data:
            repo_name = raw_data["metadata"].get("repo_name", "")
            depth_level = raw_data["metadata"].get("depth_level", 1)

        # Determine which root components are expandable
        all_expandable = expandable_components or list(sub_analyses.keys())

        return _save_analysis_unlocked(
            root_analysis,
            output_dir,
            expandable_components=all_expandable,
            sub_analyses=sub_analyses,
            repo_name=repo_name,
        )


def _detect_expanded_components_for_analysis(analysis: AnalysisInsights, output_dir: Path) -> list[str]:
    """Find components that have sub-analyses in the unified analysis.json."""
    result = _load_unified_data(output_dir)
    if result is None:
        return []

    _, sub_analyses, _ = result
    expanded: list[str] = []
    for component in analysis.components:
        if component.name in sub_analyses:
            expanded.append(component.name)
    return expanded
