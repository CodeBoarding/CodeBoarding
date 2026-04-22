import argparse
import json
import logging

from agents.llm_config import LLMConfigError
from codeboarding_cli.bootstrap import bootstrap_environment, resolve_local_run_paths
from codeboarding_workflows.incremental import run_incremental_analysis
from diagram_analysis import RunContext
from diagram_analysis.incremental.models import IncrementalSummary, IncrementalSummaryKind
from utils import monitoring_enabled

logger = logging.getLogger(__name__)


def add_arguments(subparsers: argparse._SubParsersAction, parents: list[argparse.ArgumentParser]) -> None:
    parser = subparsers.add_parser(
        "incremental",
        parents=parents,
        help="Run an incremental analysis on a local repository.",
    )
    parser.add_argument(
        "--base-ref",
        type=str,
        help=(
            "Base git ref (commit, branch, or tag) to diff against. "
            "Defaults to the commit of the previous successful CodeBoarding run "
            "stored in <output-dir>/.codeboarding; if none exists, a full analysis is required."
        ),
    )
    parser.add_argument(
        "--target-ref",
        type=str,
        help=(
            "Target git ref (commit, branch, or tag). Must match the current checkout "
            "with a clean worktree; defaults to the current working tree."
        ),
    )


def incremental_error_payload(message: str) -> dict:
    summary = IncrementalSummary(
        kind=IncrementalSummaryKind.REQUIRES_FULL_ANALYSIS,
        message=message,
        requires_full_analysis=True,
    ).to_dict()
    return {
        "mode": "incremental",
        "error": message,
        "requiresFullAnalysis": True,
        "summary": summary,
    }


def api_key_missing_payload(message: str) -> dict:
    return {
        "mode": "incremental",
        "error": message,
        "kind": "api_key_missing",
    }


def validate_arguments(args: argparse.Namespace, parser: argparse.ArgumentParser) -> None:
    if args.local is None:
        parser.error("incremental requires --local")


def run_from_args(args: argparse.Namespace, parser: argparse.ArgumentParser) -> None:
    validate_arguments(args, parser)

    repo_path, output_dir, project_name = resolve_local_run_paths(args)
    output_dir.mkdir(parents=True, exist_ok=True)

    try:
        bootstrap_environment(output_dir, args.binary_location)
    except LLMConfigError as exc:
        logger.warning("Incremental bootstrap failed: LLM provider not configured: %s", exc)
        _emit_payload(api_key_missing_payload(str(exc)))
        return
    except ValueError as exc:
        logger.exception("Incremental bootstrap failed")
        _emit_payload(incremental_error_payload(str(exc)))
        return

    try:
        run_context = RunContext.resolve(
            repo_dir=repo_path,
            project_name=project_name,
            reuse_latest_run_id=True,
        )
    except Exception as exc:
        logger.exception("RunContext resolution failed")
        payload = incremental_error_payload(str(exc))
    else:
        try:
            payload = run_incremental_analysis(
                repo_path=repo_path,
                output_dir=output_dir,
                project_name=project_name,
                depth_level=args.depth_level,
                base_ref=args.base_ref,
                target_ref=args.target_ref,
                run_id=run_context.run_id,
                log_path=run_context.log_path,
                enable_monitoring=args.enable_monitoring or monitoring_enabled(),
            )
        except Exception as exc:
            logger.exception("Incremental analysis failed")
            payload = incremental_error_payload(str(exc))
        finally:
            run_context.finalize()

    _emit_payload(payload)


def _emit_payload(payload: dict) -> None:
    print(json.dumps(payload, default=str, indent=2, sort_keys=True))
