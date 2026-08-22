from dataclasses import dataclass
from pathlib import Path

from monitoring.paths import generate_log_path
from utils import generate_run_id

# Safety-valve depth cap, not a target — see --depth-level help / README for why.
# A component that outgrows the leaf ceiling is flagged expandable at whatever depth
# the run stops, so this cap bounds how much gets expanded up front, not whether a
# large component *can* be expanded (on demand, via the partial-analysis API). Raise
# it to auto-expand deeper.
DEFAULT_DEPTH_LEVEL = 3


@dataclass(frozen=True, slots=True)
class RunPaths:
    """The repo/output locations + project name a single analysis run operates on."""

    repo_path: Path
    output_dir: Path
    project_name: str


@dataclass(frozen=True, slots=True)
class RunContext:
    """Identifiers and repo reference for a single analysis execution."""

    run_id: str
    log_path: str
    repo_dir: Path

    @classmethod
    def resolve(
        cls,
        repo_dir: Path,
        project_name: str,
    ) -> "RunContext":
        """Resolve the run metadata needed to construct a DiagramGenerator."""
        return cls(
            run_id=generate_run_id(),
            log_path=generate_log_path(project_name),
            repo_dir=repo_dir,
        )
