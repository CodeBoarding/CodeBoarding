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
    escalation_level: str = ""
    hops_used: int = 0
    trace_stop_reason: str = ""
    components_affected: int = 0
    impacted_methods_count: int = 0
    file_deltas_count: int = 0
    purely_additive: bool = False
    error: str | None = None
    phase_timings: dict[str, float] = field(default_factory=dict)

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

    # Wrap _load_incremental_baseline to capture timing
    original_load_baseline = generator._load_incremental_baseline

    def capturing_load_baseline(*args, **kwargs):
        t0 = time.perf_counter()
        result = original_load_baseline(*args, **kwargs)
        metrics.phase_timings["load_baseline"] = round(time.perf_counter() - t0, 3)
        return result

    # Wrap _compute_incremental_delta to capture delta info + timing
    original_compute_delta = generator._compute_incremental_delta

    def capturing_compute_delta(*args, **kwargs):
        t0 = time.perf_counter()
        delta = original_compute_delta(*args, **kwargs)
        metrics.phase_timings["compute_delta"] = round(time.perf_counter() - t0, 3)
        if delta is not None:
            metrics.file_deltas_count = len(delta.file_deltas)
            metrics.purely_additive = delta.is_purely_additive
        else:
            metrics.escalation_level = "no_changes"
        return delta

    # Wrap _run_semantic_trace to capture trace result + timing
    original_run_trace = generator._run_semantic_trace

    def capturing_run_trace(*args, **kwargs):
        t0 = time.perf_counter()
        result = original_run_trace(*args, **kwargs)
        metrics.phase_timings["semantic_trace"] = round(time.perf_counter() - t0, 3)
        metrics.hops_used = result.hops_used
        metrics.trace_stop_reason = result.stop_reason.value
        metrics.impacted_methods_count = len(result.all_impacted_methods)
        metrics.components_affected = len(result.impacted_components)
        return result

    # Wrap _determine_escalation to capture escalation level + timing
    original_determine_escalation = generator._determine_escalation

    def capturing_determine_escalation(*args, **kwargs):
        t0 = time.perf_counter()
        escalation = original_determine_escalation(*args, **kwargs)
        metrics.phase_timings["determine_escalation"] = round(time.perf_counter() - t0, 3)
        metrics.escalation_level = escalation.value
        return escalation

    # Wrap _save_incremental_result to capture timing
    original_save_result = generator._save_incremental_result

    def capturing_save_result(*args, **kwargs):
        t0 = time.perf_counter()
        result = original_save_result(*args, **kwargs)
        metrics.phase_timings["save_result"] = round(time.perf_counter() - t0, 3)
        return result

    # Apply patches and run
    generator._load_incremental_baseline = capturing_load_baseline  # type: ignore[method-assign]
    generator._compute_incremental_delta = capturing_compute_delta  # type: ignore[method-assign]
    generator._run_semantic_trace = capturing_run_trace  # type: ignore[method-assign]
    generator._determine_escalation = capturing_determine_escalation  # type: ignore[method-assign]
    generator._save_incremental_result = capturing_save_result  # type: ignore[method-assign]

    start = time.perf_counter()
    try:
        generator.generate_analysis_incremental()
        if (
            metrics.file_deltas_count > 0
            and not metrics.purely_additive
            and "semantic_trace" not in metrics.phase_timings
            and metrics.hops_used == 0
            and metrics.impacted_methods_count == 0
            and metrics.components_affected == 0
            and metrics.escalation_level == "none"
        ):
            metrics.escalation_level = "cosmetic_skip"
    except Exception as e:
        import traceback

        metrics.error = str(e)
        logger.error("Incremental analysis failed: %s\n%s", e, traceback.format_exc())
    finally:
        metrics.wall_clock_seconds = round(time.perf_counter() - start, 2)

    return metrics
