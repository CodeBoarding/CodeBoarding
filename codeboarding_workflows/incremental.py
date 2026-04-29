"""Workflow orchestration for commit-based incremental analysis."""

import logging
from pathlib import Path

from diagram_analysis.diagram_generator import DiagramGenerator
from diagram_analysis.io_utils import load_analysis_metadata, load_full_analysis
from repo_utils import get_git_commit_hash
from repo_utils.change_detector import detect_changes

logger = logging.getLogger(__name__)


def run_incremental_workflow(generator: DiagramGenerator) -> Path:
    """Run incremental analysis when a baseline exists, otherwise fall back to a full run."""
    existing = load_full_analysis(generator.output_dir)
    metadata = load_analysis_metadata(generator.output_dir)
    if existing is None or metadata is None:
        logger.info("No existing analysis baseline; running full analysis.")
        return generator.generate_analysis()

    base_ref = metadata.get("commit_hash", "")
    if not base_ref:
        logger.info("Baseline analysis is missing commit_hash; running full analysis.")
        return generator.generate_analysis()

    root_analysis, sub_analyses = existing
    target_ref = get_git_commit_hash(generator.repo_location)
    changes = detect_changes(generator.repo_location, base_ref, target_ref, raise_on_error=True)

    if changes.is_empty():
        return generator.output_dir.joinpath("analysis.json").resolve()

    return generator.generate_analysis_incremental(root_analysis, sub_analyses, base_ref, changes)
