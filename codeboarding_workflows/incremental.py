"""Workflow orchestration for commit-based incremental analysis."""

import logging
from pathlib import Path

from analysis_artifact.store import load_analysis_metadata, load_full_analysis
from diagram_analysis.diagram_generator import DiagramGenerator
from incremental_analysis.payload import IncrementalCompletedPayload, NoChangesPayload
from incremental_analysis.pipeline import IncrementalInputs, run_incremental_pipeline
from repo_utils import get_git_commit_hash
from repo_utils.change_detector import ChangeDetectionError
from repo_utils.diff_parser import detect_changes
from utils import ANALYSIS_FILENAME

logger = logging.getLogger(__name__)


def _inputs_from_generator(generator: DiagramGenerator) -> IncrementalInputs:
    return IncrementalInputs(
        repo_path=generator.repo_location,
        output_dir=generator.output_dir,
        repo_name=generator.repo_name,
        prepare_static_analysis=generator.prepare_static_analysis,
        build_file_coverage_summary=generator.build_file_coverage_summary,
        write_file_coverage=generator.write_file_coverage,
    )


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
    changes = detect_changes(generator.repo_location, base_ref, target_ref)
    if changes.error:
        raise ChangeDetectionError(changes.error)

    if changes.is_empty():
        return output_dir.joinpath(ANALYSIS_FILENAME).resolve()

    payload = run_incremental_pipeline(_inputs_from_generator(generator), base_ref=base_ref, target_ref=target_ref)
    if payload.requires_full_analysis:
        logger.info("Incremental workflow fell back to full analysis.")
        return generator.generate_analysis()
    if isinstance(payload, NoChangesPayload):
        return payload.analysis_path.resolve()
    if isinstance(payload, IncrementalCompletedPayload) and payload.result.analysis_path is not None:
        return payload.result.analysis_path.resolve()

    logger.info("Incremental workflow produced no analysis path; running full analysis.")
    return generator.generate_analysis()
