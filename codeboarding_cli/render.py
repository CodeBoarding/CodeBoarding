"""Standalone documentation rendering from an existing analysis.json."""

import argparse
import json
import logging
import sys
from pathlib import Path

from codeboarding_workflows.rendering import render_docs
from logging_config import setup_logging

logger = logging.getLogger(__name__)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="codeboarding-render",
        description="Render documentation from an existing CodeBoarding analysis.json",
    )
    parser.add_argument("analysis_path", type=Path, help="Path to analysis.json")
    parser.add_argument(
        "--format",
        choices=("md",),
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
    render_docs(
        analysis_path=analysis_path,
        repo_name=repo_name,
        repo_ref="",
        temp_dir=output_dir,
        format=f".{args.format}",
        root_name="overview",
    )
    logger.info("Rendered %s documentation in %s", args.format, output_dir)


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    run_from_args(parser.parse_args(sys.argv[1:] if argv is None else argv), parser)


if __name__ == "__main__":
    main()
