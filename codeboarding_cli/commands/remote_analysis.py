import argparse
import logging
from pathlib import Path

from tqdm import tqdm

from codeboarding_cli.bootstrap import bootstrap_environment
from codeboarding_workflows.remote_analysis import process_remote_repository
from diagram_analysis import RunContext
from monitoring import monitor_execution
from monitoring.paths import get_monitoring_run_dir
from repo_utils import get_repo_name, store_token
from repo_utils.ignore import initialize_codeboardingignore
from utils import CODEBOARDING_DIR_NAME, monitoring_enabled

logger = logging.getLogger(__name__)


def run_from_args(args: argparse.Namespace, parser: argparse.ArgumentParser) -> None:
    output_dir = Path.cwd() / CODEBOARDING_DIR_NAME
    output_dir.mkdir(parents=True, exist_ok=True)

    try:
        bootstrap_environment(output_dir, args.binary_location)
    except ValueError as exc:
        logger.error(str(exc))
        raise SystemExit(1) from exc
    logger.info("Starting CodeBoarding documentation generation...")

    if not args.repositories:
        logger.error("No repositories specified")
        return

    should_monitor = args.enable_monitoring or monitoring_enabled()

    if args.upload:
        try:
            store_token()
        except Exception as exc:
            logger.warning(f"Could not store GitHub token: {exc}")

    repo_root = Path("repos")
    workspace_root = Path.cwd()

    for repo in tqdm(args.repositories, desc="Generating docs for repos"):
        repo_name = get_repo_name(repo)
        repo_output_dir = workspace_root / repo_name / CODEBOARDING_DIR_NAME
        repo_output_dir.mkdir(parents=True, exist_ok=True)
        initialize_codeboardingignore(repo_output_dir)

        repo_cache_root = repo_root / repo_name
        run_context = RunContext.resolve(
            repo_dir=repo_cache_root,
            project_name=repo_name,
            reuse_latest_run_id=True,
        )

        monitoring_dir = get_monitoring_run_dir(
            run_context.log_path,
            create=should_monitor,
        )

        with monitor_execution(
            run_id=run_context.run_id,
            output_dir=str(monitoring_dir),
            enabled=should_monitor,
        ) as mon:
            mon.step(f"processing_{repo_name}")

            try:
                process_remote_repository(
                    repo_url=repo,
                    run_id=run_context.run_id,
                    log_path=run_context.log_path,
                    output_dir=repo_output_dir,
                    depth_level=args.depth_level,
                    upload=args.upload,
                    monitoring_enabled=should_monitor,
                )
            except Exception as exc:
                logger.error(f"Failed to process repository {repo}: {exc}")
                continue

    logger.info("All repositories processed successfully!")
