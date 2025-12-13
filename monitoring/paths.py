import os
from datetime import datetime
from pathlib import Path


def get_project_root() -> Path:
    project_root_env = os.getenv("PROJECT_ROOT")
    if project_root_env:
        return Path(project_root_env).resolve()
    # Fallback to current working directory
    return Path.cwd().resolve()


def get_monitoring_base_dir() -> Path:
    return get_project_root() / "runs"


def get_monitoring_runs_dir() -> Path:
    return get_monitoring_base_dir()


def get_monitoring_run_dir(run_id: str, create: bool = True) -> Path:
    runs_dir = get_monitoring_runs_dir()
    run_dir = runs_dir / run_id

    if create:
        run_dir.mkdir(parents=True, exist_ok=True)

    return run_dir


def generate_run_id(name: str) -> str:
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    return f"{name}/{timestamp}"
