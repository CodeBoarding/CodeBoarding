"""Remote-repository workflow — orchestrates a single remote analysis run.

Wraps the source/scope/run-context lifecycle with the artifact handling
the CLI needs for a remote URL: per-repo output dir under the workspace,
ignore-file bootstrap, monitoring directory wiring, doc rendering, and
markdown/JSON copy-out.
"""

import logging
from pathlib import Path

from codeboarding_workflows.analysis import run_full
from codeboarding_workflows.orchestration import run_analysis_pipeline
from codeboarding_workflows.rendering import render_docs
from codeboarding_workflows.sources import SourceContext, remote_source
from diagram_analysis import RunContext
from monitoring import monitor_execution
from monitoring.paths import get_monitoring_run_dir
from repo_utils import get_branch
from repo_utils.ignore import initialize_codeboardingignore
from utils import CODEBOARDING_DIR_NAME, copy_files

logger = logging.getLogger(__name__)


def process_one_remote(
    repo_url: str,
    workspace_root: Path,
    depth_level: int,
    upload: bool,
    should_monitor: bool,
) -> None:
    """Run a full analysis for one remote *repo_url* and emit docs under *workspace_root*."""

    def scope(src: SourceContext, run_context: RunContext) -> None:
        repo_output_dir = workspace_root / src.project_name / CODEBOARDING_DIR_NAME
        repo_output_dir.mkdir(parents=True, exist_ok=True)
        initialize_codeboardingignore(repo_output_dir)

        monitoring_dir = get_monitoring_run_dir(run_context.log_path, create=should_monitor)
        with monitor_execution(
            run_id=run_context.run_id,
            output_dir=str(monitoring_dir),
            enabled=should_monitor,
        ) as mon:
            mon.step(f"processing_{src.project_name}")
            analysis_path = run_full(
                repo_name=src.project_name,
                repo_path=src.repo_path,
                output_dir=src.artifact_dir,
                depth_level=depth_level,
                run_id=run_context.run_id,
                log_path=run_context.log_path,
                monitoring_enabled=should_monitor,
            )
            render_docs(
                analysis_path=analysis_path,
                repo_name=src.project_name,
                repo_ref=f"{repo_url}/blob/{get_branch(src.repo_path)}/",
                temp_dir=src.artifact_dir,
                format=".md",
                root_name="on_boarding",
                demo_mode=True,
            )

            artifacts = [*src.artifact_dir.glob("*.md"), *src.artifact_dir.glob("*.json")]
            if artifacts:
                copy_files(artifacts, repo_output_dir)
            else:
                logger.warning("No markdown or JSON files found in %s", src.artifact_dir)

    run_analysis_pipeline(
        source=remote_source(repo_url, upload=upload),
        scope=scope,
        reuse_latest_run_id=True,
    )
