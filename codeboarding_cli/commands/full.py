import argparse
import logging
from pathlib import Path

from tqdm import tqdm

from codeboarding_cli.bootstrap import bootstrap_environment
from codeboarding_workflows.full import process_local_repository
from codeboarding_workflows.remote import process_remote_repository
from diagram_analysis import RunContext
from monitoring import monitor_execution
from monitoring.paths import get_monitoring_run_dir
from repo_utils import get_repo_name, store_token
from repo_utils.ignore import initialize_codeboardingignore
from utils import monitoring_enabled

logger = logging.getLogger(__name__)


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "repositories",
        nargs="*",
        help="One or more Git repository URLs to generate documentation for",
    )
    parser.add_argument("--local", type=Path, help="Path to a local repository")
    parser.add_argument("--output-dir", type=Path, help="Output directory for local analysis")
    parser.add_argument("--project-name", type=str, help="Project name for local analysis")

    parser.add_argument(
        "--partial-component-id",
        type=str,
        help="Component ID to update (for partial updates only)",
    )

    parser.add_argument(
        "--binary-location",
        type=Path,
        help="Path to the binary directory for language servers (overrides ~/.codeboarding/servers/)",
    )

    parser.add_argument(
        "--depth-level",
        type=int,
        default=1,
        help="Depth level for diagram generation (default: 1)",
    )
    parser.add_argument(
        "--upload",
        action="store_true",
        help="Upload onboarding materials to GeneratedOnBoardings repo (remote repos only)",
    )
    parser.add_argument("--enable-monitoring", action="store_true", help="Enable monitoring")
    parser.add_argument(
        "--full",
        action="store_true",
        help="Force full reanalysis, skipping incremental detection and cached static analysis",
    )


def validate_arguments(args: argparse.Namespace, parser: argparse.ArgumentParser, is_local: bool) -> None:
    has_remote_repos = bool(getattr(args, "repositories", None))
    has_local_repo = args.local is not None

    if has_remote_repos == has_local_repo:
        parser.error("Provide either one or more remote repositories or --local, but not both.")

    if args.partial_component_id and not is_local:
        parser.error("--partial-component-id only works with local repositories")

    if args.output_dir and not is_local:
        parser.error("--output-dir only works with local repositories")

    if args.project_name and not is_local:
        parser.error("--project-name only works with local repositories")


def run_from_args(args: argparse.Namespace, parser: argparse.ArgumentParser) -> None:
    is_local = args.local is not None
    validate_arguments(args, parser, is_local)

    if is_local:
        local_repo_path = args.local.resolve()
        output_dir = args.output_dir.resolve() if args.output_dir else local_repo_path / ".codeboarding"
    else:
        output_dir = Path.cwd() / ".codeboarding"
        output_dir.mkdir(parents=True, exist_ok=True)

    try:
        bootstrap_environment(output_dir, args.binary_location)
    except ValueError as exc:
        logger.error(str(exc))
        raise SystemExit(1) from exc
    logger.info("Starting CodeBoarding documentation generation...")

    should_monitor = args.enable_monitoring or monitoring_enabled()

    if is_local:
        output_dir.mkdir(parents=True, exist_ok=True)
        initialize_codeboardingignore(output_dir)

        project_name = args.project_name or local_repo_path.name
        run_context = RunContext.resolve(
            repo_dir=local_repo_path,
            project_name=project_name,
            reuse_latest_run_id=args.partial_component_id is not None,
        )
        try:
            process_local_repository(
                repo_path=local_repo_path,
                output_dir=output_dir,
                project_name=project_name,
                depth_level=args.depth_level,
                component_id=args.partial_component_id,
                monitoring_enabled=should_monitor,
                incremental=False,
                force_full=args.full,
                run_id=run_context.run_id,
                log_path=run_context.log_path,
            )
        finally:
            run_context.finalize()
        logger.info(f"Documentation generated successfully in {output_dir}")
        return

    if not args.repositories:
        logger.error("No repositories specified")
        return

    if args.upload:
        try:
            store_token()
        except Exception as exc:
            logger.warning(f"Could not store GitHub token: {exc}")

    repo_root = Path("repos")
    workspace_root = Path.cwd()

    for repo in tqdm(args.repositories, desc="Generating docs for repos"):
        repo_name = get_repo_name(repo)
        repo_output_dir = workspace_root / repo_name / ".codeboarding"
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
