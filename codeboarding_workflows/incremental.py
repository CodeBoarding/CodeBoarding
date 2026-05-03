"""Workflow orchestration for cluster-driven incremental analysis."""

import logging
from pathlib import Path

from diagram_analysis.diagram_generator import DiagramGenerator
from diagram_analysis.io_utils import load_analysis_metadata, load_full_analysis
from utils import ANALYSIS_FILENAME

logger = logging.getLogger(__name__)


def run_incremental_workflow(generator: DiagramGenerator) -> Path:
    """Run incremental analysis when a baseline exists, otherwise fall back to a full run.

    Public entry point used by ``github_action.py`` and the CLI command. Shape:
    1. If no prior ``analysis.json`` is present, run full analysis.
    2. Otherwise hand the loaded baseline to ``generate_analysis_incremental``,
       which itself falls back to a full run when the cluster snapshot is
       missing or the cluster delta produces nothing actionable.
    """
    output_dir = generator.output_dir
    existing = load_full_analysis(output_dir)
    metadata = load_analysis_metadata(output_dir)
    if existing is None or metadata is None:
        logger.info("No existing analysis baseline; running full analysis.")
        return generator.generate_analysis()

    root_analysis, sub_analyses = existing

    if not root_analysis.components:
        logger.info("Baseline analysis has no components; running full analysis.")
        return generator.generate_analysis()

    return generator.generate_analysis_incremental(root_analysis, sub_analyses)


__all__ = ["run_incremental_workflow", "ANALYSIS_FILENAME"]
