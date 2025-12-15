import json
import logging
import functools
import time
import contextlib
from contextvars import ContextVar
from pathlib import Path
from typing import Callable, Any

from monitoring.stats import RunStats, current_stats

# Tracks the current high-level operation (e.g., "static_analysis", "code_generation")
current_step: ContextVar[str] = ContextVar("current_step", default="startup")

logger = logging.getLogger("monitoring")


@contextlib.contextmanager
def monitor_execution(
    run_id: str | None = None, output_dir: str = "evals/artifacts/monitoring_results", enabled: bool = True
):
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

    # Configure run-specific app.log
    app_log_path = out_path / "app.log"
    app_log_handler = logging.FileHandler(app_log_path)
    app_log_handler.setLevel(logging.DEBUG)
    app_log_formatter = logging.Formatter(
        "%(asctime)s %(levelname)-8s [%(name)s:%(lineno)d] %(message)s", datefmt="%Y-%m-%d %H:%M:%S"
    )
    app_log_handler.setFormatter(app_log_formatter)

    # Add to root logger to capture all logs
    root_logger = logging.getLogger()
    root_logger.addHandler(app_log_handler)

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
                pass

    # Initialize stats for this run
    run_stats = RunStats()
    stats_token = current_stats.set(run_stats)

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
                json.dump(run_stats.to_dict(), f, indent=2)
            logger.info(f"✨ Monitoring summary saved to {summary_file}")
        except Exception as e:
            logger.error(f"Failed to save monitoring summary: {e}")

        # Cleanup handler
        trace_logger.removeHandler(trace_handler)
        trace_handler.close()
        logger.info(f"✨ Execution traces saved to {trace_file}")

        # Cleanup app.log handler
        root_logger.removeHandler(app_log_handler)
        app_log_handler.close()

        # Reset context var
        current_stats.reset(stats_token)


def trace(step_name: str | None | Callable[..., Any] = None):
    """
    Sets the current step context and logs start/end events.
    Usage:
        @trace("analyze_source")  # Explicit name
        @trace                    # Uses function name
    """

    def _create_wrapper(func, name):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            # Set context
            token = current_step.set(name)
            start_time = time.time()

            # Log Start
            logger.info(json.dumps({"event": "step_start", "step": name, "timestamp": start_time}))

            try:
                return func(*args, **kwargs)
            except Exception as e:
                logger.error(json.dumps({"event": "step_error", "step": name, "error": str(e)}))
                raise
            finally:
                # Log End
                duration = time.time() - start_time
                logger.info(json.dumps({"event": "step_end", "step": name, "duration_ms": round(duration * 1000, 2)}))
                # Reset context
                current_step.reset(token)

        return wrapper

    # Case 1: Called as @trace (no parens) -> step_name is the function
    if callable(step_name) and not isinstance(step_name, str):
        func = step_name
        return _create_wrapper(func, func.__name__)

    # Case 2: Called as @trace("name") or @trace() -> step_name is string or None
    def decorator(func):
        final_name = step_name or func.__name__
        return _create_wrapper(func, final_name)

    return decorator
