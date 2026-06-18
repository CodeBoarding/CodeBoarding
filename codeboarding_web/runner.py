"""Drive the analysis pipeline on a background thread and stream events."""

import logging
import threading
import uuid
from collections.abc import Callable
from pathlib import Path

from codeboarding_web.diagram import load_cytoscape
from codeboarding_web.events import EventBus, TraceLogHandler
from codeboarding_web.state import RunState
from codeboarding_workflows.analysis import run_full, run_incremental
from codeboarding_workflows.orchestration import run_analysis_pipeline
from codeboarding_workflows.sources import SourceContext, local_source
from diagram_analysis import RunContext
from monitoring import monitor_execution
from monitoring.paths import get_monitoring_run_dir

logger = logging.getLogger(__name__)

_SCOPES = {"full", "incremental"}


class AnalysisRunner:
    """Owns the single background analysis run for one served repo."""

    def __init__(
        self,
        repo_path: Path,
        output_dir: Path,
        project_name: str,
        state: RunState,
        bus: EventBus,
    ) -> None:
        self.repo_path = repo_path
        self.output_dir = output_dir
        self.project_name = project_name
        self.state = state
        self.bus = bus
        self._thread: threading.Thread | None = None

    def start(self, scope: str, base_ref: str = "HEAD~1", target_ref: str = "HEAD") -> str:
        """Validate scope, claim state, and spawn the background thread; returns run_id."""
        if scope not in _SCOPES:
            raise ValueError(f"unknown scope: {scope!r}")
        run_id = uuid.uuid4().hex[:8]
        self.state.begin(run_id, scope)  # raises RunBusyError if busy
        self._thread = threading.Thread(
            target=self._run,
            args=(scope, run_id, base_ref, target_ref),
            daemon=True,
        )
        self._thread.start()
        return run_id

    def _progress_callback(self) -> None:
        """Load current Cytoscape snapshot and publish it as a diagram_delta event."""
        elements = load_cytoscape(self.output_dir, self.project_name)
        if elements is not None:
            self.bus.publish_threadsafe("diagram_delta", elements)

    def _run(self, scope: str, run_id: str, base_ref: str, target_ref: str) -> None:
        """Thread body: attach log handlers, drive pipeline, publish result, clean up."""
        handler = TraceLogHandler(self.bus)
        monitoring_logger = logging.getLogger("monitoring")
        traces_logger = logging.getLogger("traces")
        monitoring_logger.addHandler(handler)
        traces_logger.addHandler(handler)
        error: str | None = None
        try:
            self._drive_pipeline(scope, run_id, base_ref, target_ref, self._progress_callback)
        except Exception as exc:
            error = str(exc)
            logger.exception("analysis run failed")
        finally:
            monitoring_logger.removeHandler(handler)
            traces_logger.removeHandler(handler)
            self.state.finish(error=error)
            if error:
                self.bus.publish_threadsafe("run_error", {"run_id": run_id, "error": error})
            else:
                self.bus.publish_threadsafe("run_end", {"run_id": run_id})

    def _drive_pipeline(
        self,
        scope: str,
        run_id: str,
        base_ref: str,
        target_ref: str,
        progress_callback: Callable[[], None],
    ) -> None:
        """Build and run the analysis pipeline for the given scope."""

        def doc_scope(src: SourceContext, run_context: RunContext) -> None:
            monitoring_dir = get_monitoring_run_dir(run_context.log_path, create=True)
            with monitor_execution(run_id=run_context.run_id, output_dir=str(monitoring_dir), enabled=True) as mon:
                mon.step("analysis")
                if scope == "full":
                    run_full(
                        repo_name=src.project_name,
                        repo_path=src.repo_path,
                        output_dir=src.artifact_dir,
                        depth_level=1,
                        run_id=run_context.run_id,
                        log_path=run_context.log_path,
                        progress_callback=progress_callback,
                    )
                else:
                    run_incremental(
                        repo_path=src.repo_path,
                        output_dir=src.artifact_dir,
                        project_name=src.project_name,
                        run_id=run_context.run_id,
                        log_path=run_context.log_path,
                        base_ref=base_ref,
                        target_ref=target_ref,
                        progress_callback=progress_callback,
                    )

        run_analysis_pipeline(
            source=local_source(
                repo_path=self.repo_path,
                project_name=self.project_name,
                artifact_dir=self.output_dir,
            ),
            scope=doc_scope,
        )
