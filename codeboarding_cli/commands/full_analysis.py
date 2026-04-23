import argparse
import logging

from agents.llm_config import LLMConfigError
from codeboarding_cli.bootstrap import bootstrap_environment, resolve_local_run_paths
from codeboarding_cli.commands import remote_analysis
from codeboarding_workflows.local_analysis import process_local_repository
from diagram_analysis import RunContext
from repo_utils.ignore import initialize_codeboardingignore
from utils import monitoring_enabled

logger = logging.getLogger(__name__)


def add_arguments(subparsers: argparse._SubParsersAction, parents: list[argparse.ArgumentParser]) -> None:
    parser = subparsers.add_parser(
        "full",
        parents=parents,
        help="Run a full analysis on a local repository or one or more remote URLs.",
    )
    parser.add_argument(
        "repositories",
        nargs="*",
        help="One or more Git repository URLs to generate documentation for (remote mode)",
    )
    parser.add_argument(
        "--partial-component-id",
        type=str,
        help="Component ID to update (for partial updates only; requires --local)",
    )
    parser.add_argument(
        "--upload",
        action="store_true",
        help="Upload onboarding materials to GeneratedOnBoardings repo (remote only)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Force full reanalysis, skipping cached static analysis",
    )


def validate_arguments(args: argparse.Namespace, parser: argparse.ArgumentParser) -> None:
    has_remote_repos = bool(args.repositories)
    has_local_repo = args.local is not None

    if has_remote_repos == has_local_repo:
        parser.error("Provide either one or more remote repositories or --local, but not both.")

    if not has_local_repo:
        if args.partial_component_id:
            parser.error("--partial-component-id only works with --local")
        if args.output_dir:
            parser.error("--output-dir only works with --local")
        if args.project_name:
            parser.error("--project-name only works with --local")
    elif args.upload:
        parser.error("--upload only works with remote repositories")


def run_from_args(args: argparse.Namespace, parser: argparse.ArgumentParser) -> None:
    validate_arguments(args, parser)

    if args.local is None:
        remote_analysis.run_from_args(args, parser)
        return

    run_paths = resolve_local_run_paths(args)

    try:
        bootstrap_environment(run_paths.output_dir, args.binary_location)
    except LLMConfigError as exc:
        logger.error("LLM provider not configured: %s", exc)
        raise SystemExit(1) from exc
    logger.info("Starting CodeBoarding documentation generation...")

    should_monitor = args.enable_monitoring or monitoring_enabled()

    run_paths.output_dir.mkdir(parents=True, exist_ok=True)
    initialize_codeboardingignore(run_paths.output_dir)
    run_context = RunContext.resolve(
        repo_dir=run_paths.repo_path,
        project_name=run_paths.project_name,
        reuse_latest_run_id=args.partial_component_id is not None,
    )
    try:
        process_local_repository(
            repo_path=run_paths.repo_path,
            output_dir=run_paths.output_dir,
            project_name=run_paths.project_name,
            depth_level=args.depth_level,
            component_id=args.partial_component_id,
            monitoring_enabled=should_monitor,
            force_full=args.force,
            run_id=run_context.run_id,
            log_path=run_context.log_path,
        )
    finally:
        run_context.finalize()
    logger.info(f"Documentation generated successfully in {run_paths.output_dir}")
