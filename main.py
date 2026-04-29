import argparse
import sys
from pathlib import Path

from codeboarding_cli.commands import full_analysis, incremental_analysis, partial_analysis

_SUBCOMMANDS = {"full", "incremental", "partial"}


def _build_shared_parser() -> argparse.ArgumentParser:
    shared = argparse.ArgumentParser(add_help=False)
    shared.add_argument("--local", type=Path, help="Path to a local repository")
    shared.add_argument("--output-dir", type=Path, help="Output directory for local analysis")
    shared.add_argument("--project-name", type=str, help="Project name for local analysis")
    shared.add_argument(
        "--binary-location",
        type=Path,
        help="Path to the binary directory for language servers (overrides ~/.codeboarding/servers/)",
    )
    shared.add_argument(
        "--depth-level",
        type=int,
        default=1,
        help="Depth level for diagram generation (default: 1)",
    )
    shared.add_argument("--enable-monitoring", action="store_true", help="Enable monitoring")
    return shared


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="codeboarding",
        description="Generate onboarding documentation for Git repositories",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
`full` is the default command: when the first argument is not `full`,
`incremental`, or `partial`, `full` is inserted automatically.

Examples:
  # Local full analysis (output to <repo>/.codeboarding/); `full` is implied
  codeboarding --local /path/to/repo

  # Local full analysis with custom depth level
  codeboarding --local /path/to/repo --depth-level 2

  # Incremental update on a local repository (explicit subcommand required)
  codeboarding incremental --local /path/to/repo

  # Remote repository (cloned to cwd/<repo_name>/); `full` is implied
  codeboarding https://github.com/user/repo

  # Partial update (single component by ID)
  codeboarding partial --local /path/to/repo --component-id "1.2"

  # Custom binary location (e.g. VS Code extension)
  codeboarding --local /path/to/repo --binary-location /path/to/binaries
        """,
    )
    shared = _build_shared_parser()
    subparsers = parser.add_subparsers(dest="command", required=True, metavar="COMMAND")
    full_analysis.add_arguments(subparsers, parents=[shared])
    incremental_analysis.add_arguments(subparsers, parents=[shared])
    partial_analysis.add_arguments(subparsers, parents=[shared])
    return parser


def _inject_default_subcommand(argv: list[str]) -> list[str]:
    """Default to the ``full`` subcommand when the first arg isn't a subcommand or top-level help.

    Why: argparse has no first-class "default subcommand" and ``full`` takes a
    ``nargs="*"`` positional for remote repos, so a naive default would parse
    ``codeboarding incremental`` as a repo URL. Subcommand flags like ``--local``
    live on the subparser, not the top parser, so they also need ``full`` prepended.
    """
    if not argv:
        return argv
    first = argv[0]
    if first in _SUBCOMMANDS or first in {"-h", "--help"}:
        return argv
    return ["full", *argv]


def main(argv: list[str] | None = None) -> None:
    if argv is None:
        argv = sys.argv[1:]
    argv = _inject_default_subcommand(list(argv))
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "incremental":
        incremental_analysis.run_from_args(args, parser)
        return
    if args.command == "partial":
        partial_analysis.run_from_args(args, parser)
        return
    full_analysis.run_from_args(args, parser)


if __name__ == "__main__":
    main()
