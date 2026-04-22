from pathlib import Path
from typing import Any

from diagram_analysis import DiagramGenerator
from diagram_analysis.incremental.pipeline import run_incremental_pipeline
from monitoring.paths import generate_log_path
from utils import generate_run_id


def run_incremental_analysis(
    *,
    repo_path: Path,
    output_dir: Path | None = None,
    project_name: str | None = None,
    depth_level: int = 1,
    base_ref: str | None = None,
    target_ref: str | None = None,
    enable_monitoring: bool = False,
    run_id: str | None = None,
    log_path: str | None = None,
) -> dict[str, Any]:
    """Construct a generator and run the semantic incremental pipeline."""
    repo_path = repo_path.resolve()
    output_dir = (output_dir or (repo_path / ".codeboarding")).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    resolved_project_name = project_name or repo_path.name
    generator = DiagramGenerator(
        repo_location=repo_path,
        temp_folder=output_dir,
        repo_name=resolved_project_name,
        output_dir=output_dir,
        depth_level=depth_level,
        run_id=run_id or generate_run_id(),
        log_path=log_path or generate_log_path(resolved_project_name),
        monitoring_enabled=enable_monitoring,
    )
    return run_incremental_pipeline(generator, base_ref=base_ref, target_ref=target_ref)
