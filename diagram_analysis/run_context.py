from dataclasses import dataclass
from pathlib import Path

from monitoring.paths import generate_log_path
from utils import generate_run_id

# Hard cap on recursive Leiden scopes. Non-singleton communities at the cap remain
# available for the same scoped algorithm through partial analysis.
DEFAULT_DEPTH_LEVEL = 3


@dataclass(frozen=True, slots=True)
class RunPaths:
    """The repo/output locations + project name a single analysis run operates on."""

    repo_path: Path
    output_dir: Path
    project_name: str


@dataclass(frozen=True, slots=True)
class RunContext:
    """Identifiers for a single analysis execution."""

    run_id: str
    log_path: str

    @classmethod
    def resolve(cls, project_name: str) -> "RunContext":
        """Resolve the run metadata needed to construct a DiagramGenerator."""
        return cls(
            run_id=generate_run_id(),
            log_path=generate_log_path(project_name),
        )
