import argparse

from codeboarding_cli.commands import full, incremental


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Generate onboarding documentation for Git repositories",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Local repository (output written to <repo>/.codeboarding/)
  codeboarding --local /path/to/repo

  # Local repository with custom depth level
  codeboarding --local /path/to/repo --depth-level 2

  # Incremental update
  codeboarding --local /path/to/repo --incremental --json

  # Remote repository (cloned to cwd/<repo_name>/, output to cwd/<repo_name>/.codeboarding/)
  codeboarding https://github.com/user/repo

  # Partial update (update single component by ID)
  codeboarding --local /path/to/repo --partial-component-id "1.2"

  # Use custom binary location (e.g. VS Code extension)
  codeboarding --local /path/to/repo --binary-location /path/to/binaries
        """,
    )
    full.add_arguments(parser)
    incremental.add_arguments(parser)
    return parser


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.incremental:
        incremental.run_from_args(args, parser)
        return
    full.run_from_args(args, parser)
