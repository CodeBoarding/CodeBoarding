"""Standalone entry point for running health checks on a repository.

Runs static analysis and health checks only — no LLM agents or diagram generation.
Useful for testing health checks in isolation and for CI/CD health gates.

Usage:
    # Local repository (output written to <repo>/.codeboarding/health/)
    python health_main.py --local /path/to/repo

    # Remote repository (cloned to cwd/repos/, output to cwd/<repo_name>/.codeboarding/health/)
    python health_main.py https://github.com/user/repo
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


def run_health_check_local(
    repo_path: Path,
    project_name: str | None = None,
) -> None:
    """Run health checks on a local repository.

    Args:
        repo_path: Path to a local repository
        project_name: Optional project name (default: repo directory name)
    """
    resolved_repo_path = repo_path.resolve()
    if not resolved_repo_path.is_dir():
        raise ValueError(f"Repository path does not exist: {resolved_repo_path}")

    resolved_project_name = project_name or resolved_repo_path.name
    output_dir = resolved_repo_path / ".codeboarding"

    output_dir.mkdir(parents=True, exist_ok=True)
    setup_logging(log_dir=output_dir)

    _run_health_checks(resolved_repo_path, resolved_project_name, output_dir)


def run_health_check_remote(
    repo_url: str,
    project_name: str | None = None,
) -> None:
    """Run health checks on a remote repository.

    Args:
        repo_url: URL of a remote Git repository
        project_name: Optional project name (default: extracted from URL)
    """
    repo_root = Path("repos")
    repo_name = clone_repository(repo_url, repo_root)
    resolved_repo_path = repo_root / repo_name
    resolved_project_name = project_name or get_repo_name(repo_url)
    output_dir = Path.cwd() / repo_name / ".codeboarding"

    output_dir.mkdir(parents=True, exist_ok=True)
    setup_logging(log_dir=output_dir)

    _run_health_checks(resolved_repo_path, resolved_project_name, output_dir)


def _run_health_checks(repo_path: Path, project_name: str, output_dir: Path) -> None:
    """Core health check logic shared by local and remote paths."""
    logger.info(f"Running health checks on '{project_name}' at {repo_path}")

    static_analysis = get_static_analysis(repo_path)

    # Load health check configuration and initialize health config dir
    health_config_dir = output_dir / "health"
    initialize_health_dir(health_config_dir)
    health_config = load_health_config(health_config_dir)

    report = run_health_checks(static_analysis, project_name, config=health_config, repo_path=repo_path)

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
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Local repository (output written to <repo>/.codeboarding/health/)
  python health_main.py --local /path/to/repo

  # Remote repository (cloned to cwd/repos/, output to cwd/<repo_name>/.codeboarding/health/)
  python health_main.py https://github.com/user/repo

        """,
    )
    parser.add_argument(
        "repositories",
        nargs="*",
        help="One or more remote Git repository URLs to run health checks on",
    )
    parser.add_argument("--local", type=Path, help="Path to a local repository")
    parser.add_argument(
        "--project-name",
        type=str,
        default=None,
        help="Name of the project (default: extracted from repo path or URL)",
    )
    parser.add_argument(
        "--binary-location", type=Path, help="Path to the binary directory for language servers and tools"
    )

    args = parser.parse_args()

    # Validate: must provide either remote repos or --local, not both
    has_remote = bool(args.repositories)
    has_local = args.local is not None

    if has_remote == has_local:
        parser.error("Provide either one or more remote repositories or --local, but not both.")

    # Resolve binary paths for tools (tokei, LSP servers, etc.)
    if args.binary_location:
        update_config(args.binary_location)

    try:
        if has_local:
            run_health_check_local(args.local, args.project_name)
        else:
            for repo_url in args.repositories:
                run_health_check_remote(repo_url, args.project_name)
    except ValueError as e:
        parser.error(str(e))


if __name__ == "__main__":
    main()
