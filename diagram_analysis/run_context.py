from dataclasses import dataclass
from pathlib import Path

from caching.details_cache import load_existing_run_id, prune_details_caches
from monitoring.paths import generate_log_path
from utils import generate_run_id


@dataclass(frozen=True, slots=True)
class RunContext:
    """Identifiers associated with a single analysis execution."""

    run_id: str
    log_path: str


def resolve_run_context(
    repo_dir: Path,
    project_name: str,
    reuse_latest_run_id: bool = False,
) -> RunContext:
    """Resolve the run metadata needed to construct a DiagramGenerator."""
    run_id = load_existing_run_id(repo_dir) if reuse_latest_run_id else None
    if run_id is None:
        run_id = generate_run_id()

    return RunContext(
        run_id=run_id,
        log_path=generate_log_path(project_name),
    )


def finalize_run_context(repo_dir: Path, run_id: str) -> None:
    """Prune detail caches so only the current run id remains."""
    prune_details_caches(repo_dir=repo_dir, only_keep_run_id=run_id)
