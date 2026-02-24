"""Standalone entry point for running health checks on a repository.

Runs static analysis and health checks only — no LLM agents or diagram generation.
Useful for testing health checks in isolation and for CI/CD health gates.

Usage:
    # Local repository
    python health_main.py /path/to/repo --project-name MyProject --output-dir ./health_output

    # Remote repository
    python health_main.py https://github.com/user/repo --output-dir ./health_output
"""

import argparse
import logging
from pathlib import Path

from health.runner import run_health_checks
from health.config import initialize_health_dir, load_health_config
from logging_config import setup_logging
from repo_utils import clone_repository, get_repo_name
from static_analyzer import get_static_analysis
from vscode_constants import update_config

logger = logging.getLogger(__name__)


def run_health_check_command(
    repo_path: str | Path, project_name: str | None = None, output_dir: Path | None = None
) -> None:
    """Run health checks on a repository.

    Args:
        repo_path: Path to a local repository or URL of a remote Git repository
        project_name: Optional project name (extracted from repo if not provided)
        output_dir: Directory for the health report (default: ./health_output)
    """
    if output_dir is None:
        output_dir = Path("./health_output")
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    setup_logging(log_dir=output_dir)

    # Determine if repo_path is a URL or local path
    repo_input = str(repo_path)
    if repo_input.startswith(("https://", "http://", "git@", "ssh://")):
        # Remote repository URL
        repo_root = Path.cwd() / "repos"
        repo_name = clone_repository(repo_input, repo_root)
        resolved_repo_path = repo_root / repo_name
        resolved_project_name = project_name or get_repo_name(repo_input)
    else:
        # Local repository path
        resolved_repo_path = Path(repo_input).resolve()
        if not resolved_repo_path.is_dir():
            raise ValueError(f"Repository path does not exist: {resolved_repo_path}")
        resolved_project_name = project_name or resolved_repo_path.name

    logger.info(f"Running health checks on '{resolved_project_name}' at {resolved_repo_path}")

    static_analysis = get_static_analysis(resolved_repo_path)

    # Load health check configuration and initialize health config dir
    health_config_dir = output_dir / "health"
    initialize_health_dir(health_config_dir)
    health_config = load_health_config(health_config_dir)

    report = run_health_checks(
        static_analysis, resolved_project_name, config=health_config, repo_path=resolved_repo_path
    )

    if report is None:
        logger.warning("Health checks skipped: no languages found in static analysis results")
        return

    report_path = health_config_dir / "health_report.json"
    report_path.write_text(report.model_dump_json(indent=2, exclude_none=True))

    logger.info(f"Health report written to {report_path} (overall score: {report.overall_score:.3f})")


def main():
    """Main entry point that parses CLI arguments and routes to subcommands."""
    parser = argparse.ArgumentParser(
        description="Run static analysis health checks on a repository (local or remote)",
    )
    parser.add_argument(
        "repo_path", nargs="?", help="Path to a local repository or URL of a remote Git repository to analyze"
    )
    parser.add_argument(
        "--project-name",
        type=str,
        default=None,
        help="Name of the project (default: extracted from repo path or URL)",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("./health_output"),
        help="Directory for the health report (default: ./health_output)",
    )
    parser.add_argument(
        "--binary-location", type=Path, help="Path to the binary directory for language servers and tools"
    )

    args = parser.parse_args()

    if not args.repo_path:
        parser.error("Provide a repository path or URL to analyze")

    # Resolve binary paths for tools (tokei, LSP servers, etc.)
    if args.binary_location:
        update_config(args.binary_location)

    try:
        run_health_check_command(args.repo_path, args.project_name, args.output_dir)
    except ValueError as e:
        parser.error(str(e))


if __name__ == "__main__":
    main()
