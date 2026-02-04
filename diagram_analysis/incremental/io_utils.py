"""
I/O utilities for incremental analysis.

This module provides functions to load and save analysis files
and manifests during incremental updates.
"""

import json
import logging
from pathlib import Path

from agents.agent_responses import AnalysisInsights
from output_generators.markdown import sanitize
from diagram_analysis.analysis_json import from_analysis_to_json

logger = logging.getLogger(__name__)


def load_analysis(output_dir: Path) -> AnalysisInsights | None:
    """Load the main analysis.json file."""
    analysis_path = output_dir / "analysis.json"
    if not analysis_path.exists():
        return None

    try:
        with open(analysis_path, "r") as f:
            data = json.load(f)
        return AnalysisInsights.model_validate(data)
    except Exception as e:
        logger.error(f"Failed to load analysis: {e}")
        return None


def save_analysis(analysis: AnalysisInsights, output_dir: Path, expandable_components: list[str] | None = None) -> Path:
    """Save the analysis to analysis.json."""
    analysis_path = output_dir / "analysis.json"

    # Build list of Component objects for expandable check
    expandable = []
    if expandable_components:
        expandable = [c for c in analysis.components if c.name in expandable_components]

    with open(analysis_path, "w") as f:
        f.write(from_analysis_to_json(analysis, expandable))

    return analysis_path


def load_sub_analysis(output_dir: Path, component_name: str) -> AnalysisInsights | None:
    """Load a sub-analysis JSON file for a component."""
    safe_name = sanitize(component_name)
    sub_analysis_path = output_dir / f"{safe_name}.json"

    if not sub_analysis_path.exists():
        return None

    try:
        with open(sub_analysis_path, "r") as f:
            data = json.load(f)
        return AnalysisInsights.model_validate(data)
    except Exception as e:
        logger.error(f"Failed to load sub-analysis for {component_name}: {e}")
        return None


def save_sub_analysis(
    sub_analysis: AnalysisInsights,
    output_dir: Path,
    component_name: str,
    expandable_components: list[str] | None = None,
) -> Path:
    """Save a sub-analysis JSON file for a component."""
    safe_name = sanitize(component_name)
    sub_analysis_path = output_dir / f"{safe_name}.json"

    expandable = []
    if expandable_components:
        expandable = [c for c in sub_analysis.components if c.name in expandable_components]

    with open(sub_analysis_path, "w") as f:
        f.write(from_analysis_to_json(sub_analysis, expandable))

    return sub_analysis_path


def _detect_expanded_components_for_analysis(analysis: AnalysisInsights, output_dir: Path) -> list[str]:
    """Find components that already have sub-analysis JSONs on disk."""
    expanded: list[str] = []
    for component in analysis.components:
        safe_name = sanitize(component.name)
        if (output_dir / f"{safe_name}.json").exists():
            expanded.append(component.name)
    return expanded
