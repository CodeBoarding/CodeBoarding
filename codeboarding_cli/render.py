"""Standalone documentation rendering from an existing analysis.json."""

import argparse
import json
import logging
import sys

from telemetry.events import already_captured, capture_error
from pathlib import Path

from logging_config import setup_logging
from output_generators import SUPPORTED_FORMATS, render as render_output

logger = logging.getLogger(__name__)


def build_parser() -> argparse.ArgumentParser:
    """Build the standalone rendering argument parser."""
    parser = argparse.ArgumentParser(
        prog="codeboarding-render",
        description="Render documentation from an existing CodeBoarding analysis.json",
    )
    parser.add_argument("analysis_path", type=Path, help="Path to analysis.json")
    parser.add_argument(
        "--format",
        choices=SUPPORTED_FORMATS,
        default="md",
        help="Output format (default: md)",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        help="Output directory (default: directory containing analysis.json)",
    )
    return parser


def run_from_args(args: argparse.Namespace, parser: argparse.ArgumentParser) -> None:
    """Render one existing analysis according to parsed arguments."""
    analysis_path = args.analysis_path.resolve()
    if not analysis_path.is_file():
        parser.error(f"Analysis file not found: {analysis_path}")

    try:
        with analysis_path.open("r", encoding="utf-8") as analysis_file:
            analysis_data = json.load(analysis_file)
    except (OSError, json.JSONDecodeError) as exc:
        parser.error(f"Could not read analysis file '{analysis_path}': {exc}")

    metadata = analysis_data.get("metadata", {}) if isinstance(analysis_data, dict) else {}
    repo_name = metadata.get("repo_name") if isinstance(metadata, dict) else None
    if not isinstance(repo_name, str) or not repo_name:
        parser.error(f"Analysis file '{analysis_path}' is missing metadata.repo_name")

    output_dir = args.output_dir.resolve() if args.output_dir else analysis_path.parent
    output_dir.mkdir(parents=True, exist_ok=True)
    setup_logging()
    render_output(
        args.format,
        analysis_path=analysis_path,
        repo_name=repo_name,
        output_dir=output_dir,
    )
    logger.info("Rendered %s documentation in %s", args.format, output_dir)


def main(argv: list[str] | None = None) -> None:
    """Run the standalone rendering CLI."""
    parser = build_parser()
    try:
        run_from_args(parser.parse_args(sys.argv[1:] if argv is None else argv), parser)
    except BaseException as exc:
        if not already_captured(exc):
            capture_error("cli.render", exc)
        raise


if __name__ == "__main__":
    main()
