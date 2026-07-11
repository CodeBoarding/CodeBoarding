import argparse
import json
import logging
import sys
from typing import Any

from agents.llm_config import LLMConfigError
from codeboarding_cli.bootstrap import bootstrap_environment, resolve_local_run_paths
from codeboarding_cli.view_instructions import print_view_instructions
from codeboarding_workflows.analysis import BaselineUnavailableError, run_incremental
from diagram_analysis import RunContext
from diagram_analysis.run_mode import RunMode
from utils import monitoring_enabled

logger = logging.getLogger(__name__)


def add_arguments(subparsers: argparse._SubParsersAction, parents: list[argparse.ArgumentParser]) -> None:
    subparsers.add_parser(
        "incremental",
        parents=parents,
        help="Update an existing analysis for what changed since it was generated (no git needed).",
    )


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
        _emit({"mode": RunMode.INCREMENTAL, "error": str(exc), "kind": "api_key_missing"})
        return
    except ValueError as exc:
        logger.exception("Incremental bootstrap failed")
        _emit_error(str(exc))
        return

    try:
        run_context = RunContext.resolve(
            repo_dir=run_paths.repo_path,
            project_name=run_paths.project_name,
            reuse_latest_run_id=True,
        )
    except Exception as exc:
        logger.exception("RunContext resolution failed")
        _emit_error(str(exc))
        return

    try:
        analysis_path = run_incremental(
            run_paths,
            run_context,
            monitoring_enabled=args.enable_monitoring or monitoring_enabled(),
        )
    except BaselineUnavailableError as exc:
        # Expected: no baseline, or diff failed against the requested base ref.
        # The wire contract's ``requiresFullAnalysis: true`` tells the wrapper
        # to prompt for a full run; no stack trace needed.
        logger.info("Incremental unavailable: %s", exc)
        _emit_error(str(exc))
    except Exception as exc:
        logger.exception("Incremental analysis failed")
        _emit_error(str(exc))
    else:
        _emit(
            {
                "mode": RunMode.INCREMENTAL,
                "requiresFullAnalysis": False,
                "analysis_path": str(analysis_path),
            }
        )
        # Human-facing hint (logs to stderr, so the stdout JSON contract stays clean).
        print_view_instructions(analysis_path)
    finally:
        run_context.finalize()


def _emit_error(message: str) -> None:
    _emit(
        {
            "mode": RunMode.INCREMENTAL,
            "error": message,
            "requiresFullAnalysis": True,
        }
    )


def _emit(payload: dict[str, Any]) -> None:
    """Write a wire dict as JSON to stdout (the IDE/wrapper contract)."""
    sys.stdout.write(json.dumps(payload, default=str, indent=2, sort_keys=True) + "\n")
    sys.stdout.flush()
