"""Single-run state machine for the local web visualizer."""

from enum import Enum


class RunBusyError(RuntimeError):
    """Raised when a run is requested while another is in flight."""


class RunPhase(str, Enum):
    IDLE = "idle"
    RUNNING = "running"
    DONE = "done"
    ERROR = "error"


class RunState:
    """Tracks the single active analysis run (v1 allows one at a time)."""

    def __init__(self) -> None:
        self.phase: RunPhase = RunPhase.IDLE
        self.run_id: str | None = None
        self.scope: str | None = None
        self.error: str | None = None

    @property
    def is_busy(self) -> bool:
        return self.phase is RunPhase.RUNNING

    def begin(self, run_id: str, scope: str) -> None:
        if self.is_busy:
            raise RunBusyError("an analysis run is already in progress")
        self.phase = RunPhase.RUNNING
        self.run_id = run_id
        self.scope = scope
        self.error = None

    def finish(self, error: str | None = None) -> None:
        self.phase = RunPhase.ERROR if error else RunPhase.DONE
        self.error = error
