"""
Context managers and decorators for monitoring execution flow.
"""

import json
import logging
import functools
import time
import contextlib
from contextvars import ContextVar
from pathlib import Path

from monitoring.stats import stats

# Tracks the current high-level operation (e.g., "static_analysis", "code_generation")
current_step: ContextVar[str] = ContextVar("current_step", default="startup")

logger = logging.getLogger("monitoring")


@contextlib.contextmanager
def monitor_execution(run_id: str | None = None, output_dir: str = "evals/artifacts/monitoring_results", enabled: bool = True):
    """
    Context manager that handles the entire monitoring lifecycle.
    - Sets up JSONL streaming for trace events
    - Captures global stats
    - Saves summary on exit (even on error)
    - If enabled=False, returns a dummy context manager
    """
    # Dummy context for when monitoring is disabled
    if not enabled:

        class DummyContext:
            def step(self, name):
                pass

            def end_step(self):
                pass

        yield DummyContext()
        return

    # Default run_id if none provided
    if not run_id:
        from uuid import uuid4

        run_id = str(uuid4())[:8]

    # Ensure output directory exists
    out_path = Path(output_dir)
    out_path.mkdir(parents=True, exist_ok=True)

    # Setup Streaming - use trace.jsonl in run directory
    trace_file = out_path / "trace.jsonl"

    # Configure dedicated trace logger
    trace_logger = logging.getLogger("traces")
    trace_logger.setLevel(logging.INFO)
    trace_logger.propagate = False

    # File handler for streaming JSONL
    trace_handler = logging.FileHandler(trace_file)
    trace_handler.setFormatter(logging.Formatter("%(message)s"))
    trace_logger.addHandler(trace_handler)

    # Allow the user to manually log steps via the yielded context
    class MonitorContext:
        def step(self, name):
            trace_logger.info(json.dumps({"event": "phase_change", "step": name, "timestamp": time.time()}))
            # Also update the ContextVar for other components
            self._token = current_step.set(name)

        def end_step(self):
            # Reset if needed, though trace_step decorator usually handles this
            pass

    try:
        # Log start of run
        trace_logger.info(json.dumps({"event": "run_start", "run_id": run_id, "timestamp": time.time()}))

        yield MonitorContext()

    finally:
        # Log end of run
        trace_logger.info(json.dumps({"event": "run_end", "run_id": run_id, "timestamp": time.time()}))

        # Cleanup & Save Summary (Happens automatically on exit/crash)
        summary_file = out_path / f"summary_{run_id}.json"
        try:
            with open(summary_file, "w") as f:
                json.dump(stats.to_dict(), f, indent=2)
            logger.info(f"✨ Monitoring summary saved to {summary_file}")
        except Exception as e:
            logger.error(f"Failed to save monitoring summary: {e}")

        # Cleanup handler
        trace_logger.removeHandler(trace_handler)
        trace_handler.close()
        logger.info(f"✨ Execution traces saved to {trace_file}")


def trace_step(step_name: str):
    """
    Sets the current step context and logs start/end events.
    Usage: @trace_step("analyze_source")
    """

    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            # Set context
            token = current_step.set(step_name)
            start_time = time.time()

            # Log Start
            logger.info(json.dumps({"event": "step_start", "step": step_name, "timestamp": start_time}))

            try:
                return func(*args, **kwargs)
            except Exception as e:
                logger.error(json.dumps({"event": "step_error", "step": step_name, "error": str(e)}))
                raise
            finally:
                # Log End
                duration = time.time() - start_time
                logger.info(
                    json.dumps({"event": "step_end", "step": step_name, "duration_ms": round(duration * 1000, 2)})
                )
                # Reset context
                current_step.reset(token)

        return wrapper

    return decorator
