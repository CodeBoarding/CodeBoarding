import argparse
import logging

from agents.llm_config import LLMConfigError
from codeboarding_cli.bootstrap import bootstrap_environment, resolve_local_run_paths
from codeboarding_workflows.analysis import run_partial
from codeboarding_workflows.orchestration import run_analysis_pipeline
from codeboarding_workflows.sources import SourceContext, local_source
from diagram_analysis import RunContext
from repo_utils.ignore import initialize_codeboardingignore

logger = logging.getLogger(__name__)


def add_arguments(subparsers: argparse._SubParsersAction, parents: list[argparse.ArgumentParser]) -> None:
    parser = subparsers.add_parser(
        "partial",
        parents=parents,
        help="Update a single component in an existing analysis of a local repository.",
    )
    parser.add_argument(
        "--component-id",
        type=str,
        required=True,
        help="ID of the component to update (e.g. '1.2').",
    )


def validate_arguments(args: argparse.Namespace, parser: argparse.ArgumentParser) -> None:
    if args.local is None:
        parser.error("partial requires --local")


def run_from_args(args: argparse.Namespace, parser: argparse.ArgumentParser) -> None:
    validate_arguments(args, parser)

    run_paths = resolve_local_run_paths(args)

    try:
        bootstrap_environment(run_paths.output_dir, args.binary_location)
    except LLMConfigError as exc:
        logger.error("LLM provider not configured: %s", exc)
        raise SystemExit(1) from exc
    logger.info("Starting CodeBoarding partial component update...")

    run_paths.output_dir.mkdir(parents=True, exist_ok=True)
    initialize_codeboardingignore(run_paths.output_dir)

    def scope(src: SourceContext, run_context: RunContext) -> None:
        run_partial(
            repo_path=src.repo_path,
            output_dir=src.artifact_dir,
            project_name=src.project_name,
            component_id=args.component_id,
            depth_level=args.depth_level,
            run_id=run_context.run_id,
            log_path=run_context.log_path,
        )

    run_analysis_pipeline(
        source=local_source(
            repo_path=run_paths.repo_path,
            project_name=run_paths.project_name,
            artifact_dir=run_paths.output_dir,
        ),
        scope=scope,
        reuse_latest_run_id=True,
    )
    logger.info(f"Component '{args.component_id}' updated in {run_paths.output_dir}")
