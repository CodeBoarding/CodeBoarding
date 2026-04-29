import argparse
import json
import logging
import sys

from agents.llm_config import LLMConfigError
from codeboarding_cli.bootstrap import bootstrap_environment, resolve_local_run_paths
from codeboarding_workflows.analysis import run_incremental
from diagram_analysis import RunContext
from diagram_analysis.incremental_payload import RequiresFullAnalysisPayload, IncrementalRunPayload
from diagram_analysis.run_metadata import RunMode
from diagram_analysis.run_metadata import last_successful_commit
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
            "Git ref (commit SHA, branch, or tag) to diff against when computing the incremental update. "
            "Defaults to the commit that produced the last successful analysis "
            "(recorded in <output-dir>/incremental_run_metadata.json); "
            "if no prior run is recorded, incremental mode falls back to a full analysis."
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


def _error_payload(message: str) -> RequiresFullAnalysisPayload:
    """Build a ``REQUIRES_FULL_ANALYSIS`` payload for a CLI-level error."""
    return RequiresFullAnalysisPayload(message=message, error=message)


def _api_key_missing_dict(message: str) -> dict:
    """Distinct wire shape — the wrapper detects ``kind=api_key_missing`` to prompt the user."""
    return {
        "mode": RunMode.INCREMENTAL,
        "error": message,
        "kind": "api_key_missing",
    }


def validate_arguments(args: argparse.Namespace, parser: argparse.ArgumentParser) -> None:
    if args.local is None:
        parser.error("incremental requires --local")


def run_from_args(args: argparse.Namespace, parser: argparse.ArgumentParser) -> None:
    validate_arguments(args, parser)

    run_paths = resolve_local_run_paths(args)
    run_paths.output_dir.mkdir(parents=True, exist_ok=True)

    try:
        bootstrap_environment(run_paths.output_dir, args.binary_location)
    except LLMConfigError as exc:
        logger.warning("Incremental bootstrap failed: LLM provider not configured: %s", exc)
        _emit_dict(_api_key_missing_dict(str(exc)))
        return
    except ValueError as exc:
        logger.exception("Incremental bootstrap failed")
        _emit_payload(_error_payload(str(exc)))
        return

    # Resolve base_ref up-front so every layer below sees a concrete str.
    base_ref = args.base_ref or last_successful_commit(run_paths.output_dir)
    if not base_ref:
        msg = "No prior incremental metadata found; full analysis required before running incremental."
        logger.info(
            "Incremental aborted (base_ref=<unresolved> target_ref=%s): %s",
            args.target_ref or "WORKTREE",
            msg,
        )
        _emit_payload(RequiresFullAnalysisPayload(message=msg))
        return

    target_ref = args.target_ref or ""

    try:
        run_context = RunContext.resolve(
            repo_dir=run_paths.repo_path,
            project_name=run_paths.project_name,
            reuse_latest_run_id=True,
        )
    except Exception as exc:
        logger.exception("RunContext resolution failed")
        payload = _error_payload(str(exc))
    else:
        try:
            payload = run_incremental(
                repo_path=run_paths.repo_path,
                output_dir=run_paths.output_dir,
                project_name=run_paths.project_name,
                depth_level=args.depth_level,
                base_ref=base_ref,
                target_ref=target_ref,
                run_id=run_context.run_id,
                log_path=run_context.log_path,
                monitoring_enabled=args.enable_monitoring or monitoring_enabled(),
            )
        except Exception as exc:
            logger.exception("Incremental analysis failed")
            payload = _error_payload(str(exc))
        finally:
            run_context.finalize()

    _emit_payload(payload)


def _emit_payload(payload: IncrementalRunPayload) -> None:
    """Serialize *payload* as JSON on stdout (the CLI's wire contract)."""
    _emit_dict(payload.to_dict())


def _emit_dict(payload: dict) -> None:
    """Write a pre-built wire dict as JSON to stdout.

    Why: the wrapper / IDE integration reads stdout as JSON. This is the
    incremental command's API contract, not a diagnostic log line.
    """
    sys.stdout.write(json.dumps(payload, default=str, indent=2, sort_keys=True) + "\n")
    sys.stdout.flush()
