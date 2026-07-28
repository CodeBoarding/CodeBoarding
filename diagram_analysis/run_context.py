import logging
from dataclasses import dataclass
from pathlib import Path

from caching.details_cache import FinalAnalysisCache, prune_details_caches
from monitoring.paths import generate_log_path
from utils import generate_run_id

logger = logging.getLogger(__name__)

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
        reuse_latest_run_id: bool = False,
    ) -> "RunContext":
        """Resolve the run metadata needed to construct a DiagramGenerator."""
        run_id = _load_existing_run_id(repo_dir) if reuse_latest_run_id else None
        if run_id is None:
            run_id = generate_run_id()

        return cls(
            run_id=run_id,
            log_path=generate_log_path(project_name),
            repo_dir=repo_dir,
        )

    def finalize(self) -> None:
        """Prune detail caches so only the current run id remains."""
        prune_details_caches(repo_dir=self.repo_dir, only_keep_run_id=self.run_id)


def _load_existing_run_id(repo_dir: Path) -> str | None:
    """Check details caches for the most recent run_id."""
    if not (repo_dir / ".git").exists():
        logger.info("Repo not yet cloned at %s; skipping run_id cache lookup", repo_dir)
        return None
    latest = FinalAnalysisCache(repo_dir).load_most_recent_run()
    if latest is None:
        logger.info("No existing run_id found in details caches")
        return None

    run_id, updated_at = latest
    logger.info("Reusing most recent run_id=%s (updated_at=%d)", run_id, updated_at)
    return run_id
