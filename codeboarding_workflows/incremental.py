"""Workflow orchestration for commit-based incremental analysis."""

import logging
from pathlib import Path

from diagram_analysis.diagram_generator import DiagramGenerator
from diagram_analysis.incremental_payload import IncrementalCompletedPayload, NoChangesPayload
from diagram_analysis.incremental_pipeline import run_incremental_pipeline
from diagram_analysis.io_utils import load_analysis_metadata, load_full_analysis
from repo_utils import get_git_commit_hash
from repo_utils.change_detector import ChangeDetectionError, ChangeSet
from repo_utils.diff_parser import detect_changes as _detect_changes
from utils import ANALYSIS_FILENAME

logger = logging.getLogger(__name__)


def detect_changes(
    repo_dir: Path,
    base_ref: str,
    target_ref: str,
    raise_on_error: bool = False,
) -> ChangeSet:
    """Compatibility wrapper around ``repo_utils.diff_parser.detect_changes``."""
    change_set = _detect_changes(repo_dir, base_ref, target_ref)
    if change_set.error and raise_on_error:
        raise ChangeDetectionError(change_set.error)
    return change_set


def run_incremental_workflow(generator: DiagramGenerator) -> Path:
    """Run incremental analysis when a baseline exists, otherwise fall back to a full run."""
    output_dir = generator.output_dir
    existing = load_full_analysis(output_dir)
    metadata = load_analysis_metadata(generator.output_dir)
    if existing is None or metadata is None:
        logger.info("No existing analysis baseline; running full analysis.")
        return generator.generate_analysis()

    base_ref = metadata.get("commit_hash", "")
    if not base_ref:
        logger.info("Baseline analysis is missing commit_hash; running full analysis.")
        return generator.generate_analysis()

    target_ref = get_git_commit_hash(generator.repo_location)
    changes = detect_changes(generator.repo_location, base_ref, target_ref, raise_on_error=True)

    if changes.is_empty():
        return output_dir.joinpath(ANALYSIS_FILENAME).resolve()

    payload = run_incremental_pipeline(generator, base_ref=base_ref, target_ref=target_ref)
    if payload.requires_full_analysis:
        logger.info("Incremental workflow fell back to full analysis.")
        return generator.generate_analysis()
    if isinstance(payload, NoChangesPayload):
        return payload.analysis_path.resolve()
    if isinstance(payload, IncrementalCompletedPayload) and payload.result.analysis_path is not None:
        return payload.result.analysis_path.resolve()

    logger.info("Incremental workflow produced no analysis path; running full analysis.")
    return generator.generate_analysis()
