import logging
from pathlib import Path

from codeboarding_workflows.full_analysis import generate_analysis
from codeboarding_workflows.partial_analysis import partial_update

logger = logging.getLogger(__name__)


def process_local_repository(
    repo_path: Path,
    output_dir: Path,
    project_name: str,
    run_id: str,
    log_path: str,
    depth_level: int = 1,
    component_id: str | None = None,
    monitoring_enabled: bool = False,
    force_full: bool = False,
) -> None:
    if component_id:
        partial_update(
            repo_path=repo_path,
            output_dir=output_dir,
            project_name=project_name,
            component_id=component_id,
            depth_level=depth_level,
            run_id=run_id,
            log_path=log_path,
        )
        return

    generate_analysis(
        repo_name=project_name,
        repo_path=repo_path,
        output_dir=output_dir,
        depth_level=depth_level,
        run_id=run_id,
        log_path=log_path,
        monitoring_enabled=monitoring_enabled,
        force_full=force_full,
    )
