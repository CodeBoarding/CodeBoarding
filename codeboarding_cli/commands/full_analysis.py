import argparse
import logging
from pathlib import Path

from tqdm import tqdm

from agents.llm_config import LLMConfigError
from codeboarding_cli.bootstrap import bootstrap_environment, resolve_local_run_paths
from codeboarding_workflows.analysis import run_full
from codeboarding_workflows.orchestration import run_analysis_pipeline
from codeboarding_workflows.rendering import render_docs
from codeboarding_workflows.sources import SourceContext, local_source, remote_source
from diagram_analysis import RunContext
from monitoring import monitor_execution
from monitoring.paths import get_monitoring_run_dir
from repo_utils import get_branch, store_token
from repo_utils.ignore import initialize_codeboardingignore
from utils import CODEBOARDING_DIR_NAME, copy_files, monitoring_enabled

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
        if args.output_dir:
            parser.error("--output-dir only works with --local")
        if args.project_name:
            parser.error("--project-name only works with --local")
    elif args.upload:
        parser.error("--upload only works with remote repositories")


def run_from_args(args: argparse.Namespace, parser: argparse.ArgumentParser) -> None:
    validate_arguments(args, parser)

    if args.local is None:
        _run_remote(args)
    else:
        _run_local(args)


def _run_local(args: argparse.Namespace) -> None:
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

    def scope(src: SourceContext, run_context: RunContext) -> None:
        run_full(
            repo_name=src.project_name,
            repo_path=src.repo_path,
            output_dir=src.artifact_dir,
            depth_level=args.depth_level,
            run_id=run_context.run_id,
            log_path=run_context.log_path,
            monitoring_enabled=should_monitor,
            force_full=args.force,
        )

    run_analysis_pipeline(
        source=local_source(
            repo_path=run_paths.repo_path,
            project_name=run_paths.project_name,
            artifact_dir=run_paths.output_dir,
        ),
        scope=scope,
    )
    logger.info(f"Documentation generated successfully in {run_paths.output_dir}")


def _run_remote(args: argparse.Namespace) -> None:
    output_dir = Path.cwd() / CODEBOARDING_DIR_NAME
    output_dir.mkdir(parents=True, exist_ok=True)

    try:
        bootstrap_environment(output_dir, args.binary_location)
    except LLMConfigError as exc:
        logger.error("LLM provider not configured: %s", exc)
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

    workspace_root = Path.cwd()

    for repo_url in tqdm(args.repositories, desc="Generating docs for repos"):
        try:
            _process_one_remote(
                repo_url=repo_url,
                workspace_root=workspace_root,
                depth_level=args.depth_level,
                upload=args.upload,
                should_monitor=should_monitor,
            )
        except Exception as exc:
            logger.error(f"Failed to process repository {repo_url}: {exc}")
            continue

    logger.info("All repositories processed successfully!")


def _process_one_remote(
    repo_url: str,
    workspace_root: Path,
    depth_level: int,
    upload: bool,
    should_monitor: bool,
) -> None:
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
