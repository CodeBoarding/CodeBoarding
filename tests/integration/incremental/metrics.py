"""Metric capture for incremental analysis benchmark runs.

Wraps DiagramGenerator to intercept internal pipeline return values
(delta, trace result, escalation level) without modifying the source.
"""

import logging
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from unittest.mock import patch

from dotenv import load_dotenv

# Ensure LLM API keys are available
load_dotenv(Path(__file__).parent.parent.parent.parent / ".env")

from diagram_analysis.diagram_generator import DiagramGenerator

logger = logging.getLogger(__name__)


@dataclass
class IncrementalRunMetrics:
    """Metrics captured from a single incremental analysis run."""

    wall_clock_seconds: float = 0.0
    outcome: str = ""
    hops_used: int = 0
    trace_stop_reason: str = ""
    components_affected: int = 0
    impacted_methods_count: int = 0
    file_deltas_count: int = 0
    purely_additive: bool = False
    error: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


def run_incremental_with_metrics(
    repo_dir: Path,
    output_dir: Path,
    project_name: str = "deepface",
) -> IncrementalRunMetrics:
    """Run incremental analysis on repo_dir and capture pipeline metrics.

    Creates a DiagramGenerator, monkey-patches its internal methods to
    intercept return values, then runs generate_analysis_incremental().
    """
    metrics = IncrementalRunMetrics()
    run_id = uuid.uuid4().hex
    log_path = str(output_dir / "logs" / f"{project_name}.log")

    generator = DiagramGenerator(
        repo_location=repo_dir,
        temp_folder=output_dir,
        repo_name=project_name,
        output_dir=output_dir,
        depth_level=1,
        run_id=run_id,
        log_path=log_path,
        monitoring_enabled=False,
    )

    # Wrap _compute_incremental_delta to capture delta info
    original_compute_delta = generator._compute_incremental_delta

    def capturing_compute_delta(*args, **kwargs):
        delta = original_compute_delta(*args, **kwargs)
        if delta is not None:
            metrics.file_deltas_count = len(delta.file_deltas)
            metrics.purely_additive = delta.is_purely_additive
            if delta.is_purely_additive:
                metrics.outcome = "skip"
        else:
            metrics.outcome = "no_change"
        return delta

    # Wrap _run_semantic_trace to capture trace result
    original_run_trace = generator._run_semantic_trace

    def capturing_run_trace(*args, **kwargs):
        result = original_run_trace(*args, **kwargs)
        metrics.hops_used = result.hops_used
        metrics.trace_stop_reason = result.stop_reason.value
        metrics.impacted_methods_count = len(result.all_impacted_methods)
        metrics.components_affected = len(result.impacted_components)
        return result

    # Wrap _determine_escalation to capture outcome
    original_determine_escalation = generator._determine_escalation

    def capturing_determine_escalation(*args, **kwargs):
        escalation = original_determine_escalation(*args, **kwargs)
        # Map internal escalation to benchmark outcome:
        #   no_change  - no diff detected, analysis untouched
        #   skip       - additive/cosmetic, no patch needed
        #   patch      - delta applied, JSON patched in-place (LLM)
        #   reexpand   - DetailsAgent re-ran on affected components
        #   full       - requires full analysis
        if escalation.value in ("root", "full"):
            metrics.outcome = "full"
        elif escalation.value == "scoped":
            metrics.outcome = "reexpand"
        elif metrics.components_affected > 0:
            metrics.outcome = "patch"
        else:
            metrics.outcome = "skip"
        return escalation

    # Apply patches and run
    generator._compute_incremental_delta = capturing_compute_delta  # type: ignore[method-assign]
    generator._run_semantic_trace = capturing_run_trace  # type: ignore[method-assign]
    generator._determine_escalation = capturing_determine_escalation  # type: ignore[method-assign]

    start = time.perf_counter()
    try:
        generator.generate_analysis_incremental()
    except Exception as e:
        import traceback

        metrics.error = str(e)
        logger.error("Incremental analysis failed: %s\n%s", e, traceback.format_exc())
    finally:
        metrics.wall_clock_seconds = round(time.perf_counter() - start, 2)

    return metrics
