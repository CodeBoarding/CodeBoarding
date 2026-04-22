import argparse
import json
import logging

from codeboarding_cli.bootstrap import bootstrap_environment
from codeboarding_workflows.incremental import run_incremental_analysis
from diagram_analysis import RunContext
from diagram_analysis.incremental_models import IncrementalSummary, IncrementalSummaryKind
from utils import monitoring_enabled

logger = logging.getLogger(__name__)


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--incremental", action="store_true", help="Use incremental analysis")
    parser.add_argument("--base-ref", type=str, help="Base git ref/tree. Defaults to the last successful run.")
    parser.add_argument(
        "--target-ref",
        type=str,
        help=(
            "Target git ref/tree. Must match the current checkout with a clean worktree; "
            "defaults to the current working tree."
        ),
    )
    parser.add_argument("--json", action="store_true", help="Emit structured JSON on stdout")


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
        parser.error("--incremental requires --local")
    if getattr(args, "repositories", None):
        parser.error("--incremental only works with --local repositories")
    if args.partial_component_id:
        parser.error("--partial-component-id cannot be combined with --incremental")
    if args.upload:
        parser.error("--upload cannot be combined with --incremental")


def run_from_args(args: argparse.Namespace, parser: argparse.ArgumentParser) -> None:
    validate_arguments(args, parser)

    repo_path = args.local.resolve()
    output_dir = args.output_dir.resolve() if args.output_dir else repo_path / ".codeboarding"
    output_dir.mkdir(parents=True, exist_ok=True)

    try:
        bootstrap_environment(output_dir, args.binary_location)
    except ValueError as exc:
        logger.warning("Incremental bootstrap failed: %s", exc)
        _emit_payload(api_key_missing_payload(str(exc)), args.json)
        return

    project_name = args.project_name or repo_path.name
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

    _emit_payload(payload, args.json)


def _emit_payload(payload: dict, emit_json: bool) -> None:
    if emit_json:
        print(json.dumps(payload, default=str, indent=2, sort_keys=True))
        return

    summary = payload.get("summary")
    if isinstance(summary, dict) and summary.get("message"):
        print(summary["message"])
    elif payload.get("error"):
        print(payload["error"])
    else:
        print(json.dumps(payload, default=str, sort_keys=True))
