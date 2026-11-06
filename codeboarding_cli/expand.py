"""Standalone expansion of a stored analysis.json into its fully self-describing form."""

import argparse
import json
import logging
import sys
from pathlib import Path

from diagram_analysis.analysis_json import expand_analysis_document
from logging_config import setup_logging

logger = logging.getLogger(__name__)


def build_parser() -> argparse.ArgumentParser:
    """Build the standalone expansion argument parser."""
    parser = argparse.ArgumentParser(
        prog="codeboarding-expand",
        description=(
            "Expand a stored analysis.json: resolve interned method ids back to "
            "'<file_path>|<qualified_name>' keys and write out every field the stored "
            "form derives. Reads on stdin and writes to stdout when no paths are given."
        ),
    )
    parser.add_argument(
        "analysis_path",
        type=Path,
        nargs="?",
        help="Path to analysis.json (default: stdin)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="Where to write the expanded document (default: stdout)",
    )
    parser.add_argument(
        "--indent",
        type=int,
        default=2,
        help="JSON indentation; 0 writes it minified (default: 2)",
    )
    return parser


def run_from_args(args: argparse.Namespace, parser: argparse.ArgumentParser) -> None:
    """Expand one stored analysis according to parsed arguments."""
    if args.analysis_path is None:
        try:
            data = json.load(sys.stdin)
        except json.JSONDecodeError as exc:
            parser.error(f"Could not parse analysis JSON on stdin: {exc}")
    else:
        analysis_path = args.analysis_path.resolve()
        if not analysis_path.is_file():
            parser.error(f"Analysis file not found: {analysis_path}")
        try:
            with analysis_path.open("r", encoding="utf-8") as analysis_file:
                data = json.load(analysis_file)
        except (OSError, json.JSONDecodeError) as exc:
            parser.error(f"Could not read analysis file '{analysis_path}': {exc}")

    if not isinstance(data, dict):
        parser.error("Analysis JSON must be an object")

    try:
        expanded = expand_analysis_document(data)
    except ValueError as exc:
        parser.error(f"Could not expand analysis: {exc}")

    payload = json.dumps(expanded, indent=args.indent or None)
    if args.output is None:
        sys.stdout.write(payload + "\n")
        return

    output = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(payload + "\n", encoding="utf-8")
    setup_logging()
    logger.info("Expanded analysis written to %s", output)


def main(argv: list[str] | None = None) -> None:
    """Run the standalone expansion CLI."""
    parser = build_parser()
    run_from_args(parser.parse_args(sys.argv[1:] if argv is None else argv), parser)


if __name__ == "__main__":
    main()
