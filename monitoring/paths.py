import os
from datetime import datetime
from pathlib import Path


def get_monitoring_base_dir() -> Path:
    return Path(os.getenv("PROJECT_ROOT")) / "runs"


def get_monitoring_run_dir(run_id: str, create: bool = True) -> Path:
    runs_dir = get_monitoring_base_dir()
    run_dir = runs_dir / run_id

    if create:
        run_dir.mkdir(parents=True, exist_ok=True)

    return run_dir


def generate_run_id(name: str) -> str:
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    return f"{name}/{timestamp}"


def get_latest_run_dir(project_name: str) -> Path | None:
    """Find the most recent monitoring run directory for a project."""
    runs_dir = get_monitoring_base_dir()

    if not runs_dir.exists():
        return None

    # Look for the project directory first (format: runs/{project_name})
    project_run_dir = runs_dir / project_name

    if not project_run_dir.exists() or not project_run_dir.is_dir():
        return None

    # Find the latest timestamped subdirectory
    timestamps = sorted(
        [d for d in project_run_dir.iterdir() if d.is_dir()],
        key=lambda x: x.name,
        reverse=True,
    )

    return timestamps[0] if timestamps else None
