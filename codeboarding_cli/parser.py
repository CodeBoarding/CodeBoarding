import argparse
from pathlib import Path

from codeboarding_cli.commands import full, incremental


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
Examples:
  # Local full analysis (output to <repo>/.codeboarding/)
  codeboarding full --local /path/to/repo

  # Local full analysis with custom depth level
  codeboarding full --local /path/to/repo --depth-level 2

  # Incremental update on a local repository
  codeboarding incremental --local /path/to/repo

  # Remote repository (cloned to cwd/<repo_name>/)
  codeboarding full https://github.com/user/repo

  # Partial update (single component by ID)
  codeboarding full --local /path/to/repo --partial-component-id "1.2"

  # Custom binary location (e.g. VS Code extension)
  codeboarding full --local /path/to/repo --binary-location /path/to/binaries
        """,
    )
    shared = _build_shared_parser()
    subparsers = parser.add_subparsers(dest="command", required=True, metavar="COMMAND")
    full.add_arguments(subparsers, parents=[shared])
    incremental.add_arguments(subparsers, parents=[shared])
    return parser


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "incremental":
        incremental.run_from_args(args, parser)
        return
    full.run_from_args(args, parser)
