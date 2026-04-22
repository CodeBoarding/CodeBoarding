import logging
from pathlib import Path

from diagram_analysis import DiagramGenerator
from diagram_analysis.run_metadata import write_last_run_metadata

logger = logging.getLogger(__name__)


def generate_analysis(
    repo_name: str,
    repo_path: Path,
    output_dir: Path,
    run_id: str,
    log_path: str,
    depth_level: int = 1,
    monitoring_enabled: bool = False,
    force_full: bool = False,
) -> Path:
    generator = DiagramGenerator(
        repo_location=repo_path,
        temp_folder=output_dir,
        repo_name=repo_name,
        output_dir=output_dir,
        depth_level=depth_level,
        run_id=run_id,
        log_path=log_path,
        monitoring_enabled=monitoring_enabled,
    )
    generator.force_full_analysis = force_full
    analysis_path = generator.generate_analysis()
    write_last_run_metadata(output_dir, repo_path, mode="full", analysis_path=analysis_path)
    return analysis_path
