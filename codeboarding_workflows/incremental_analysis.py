from pathlib import Path

from diagram_analysis import DiagramGenerator
from diagram_analysis.incremental.payload import IncrementalRunPayload
from diagram_analysis.incremental.pipeline import run_incremental_pipeline


def run_incremental_analysis(
    repo_path: Path,
    output_dir: Path,
    project_name: str,
    run_id: str,
    log_path: str,
    depth_level: int = 1,
    base_ref: str | None = None,
    target_ref: str | None = None,
    enable_monitoring: bool = False,
) -> IncrementalRunPayload:
    """Construct a generator and run the semantic incremental pipeline.

    All path/name/run-id arguments are required and must already be resolved
    by the caller (``resolve_local_run_paths`` + ``RunContext.resolve``).
    ``base_ref`` and ``target_ref`` stay optional because the pipeline
    derives them from metadata or the worktree when not supplied.
    """
    generator = DiagramGenerator(
        repo_location=repo_path,
        temp_folder=output_dir,
        repo_name=project_name,
        output_dir=output_dir,
        project_name=project_name,
        depth_level=depth_level,
        run_id=run_id,
        log_path=log_path,
        monitoring_enabled=enable_monitoring,
    )
    return run_incremental_pipeline(generator, base_ref=base_ref, target_ref=target_ref)
