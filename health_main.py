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
import os
from pathlib import Path

from health.runner import run_health_checks
from logging_config import setup_logging
from repo_utils import clone_repository, get_repo_name
from static_analyzer import get_static_analysis
from vscode_constants import update_config

logger = logging.getLogger(__name__)


def main():
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

    output_dir: Path = args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    setup_logging(log_dir=output_dir)

    # Resolve binary paths for tools (tokei, LSP servers, etc.)
    if args.binary_location:
        update_config(args.binary_location)

    # Determine if repo_path is a URL or local path
    repo_input = args.repo_path
    if repo_input.startswith(("https://", "http://", "git@", "ssh://")):
        # Remote repository URL
        repo_root = Path(os.getenv("REPO_ROOT", "repos"))
        repo_name = clone_repository(repo_input, repo_root)
        repo_path = repo_root / repo_name
        project_name = args.project_name or get_repo_name(repo_input)
    else:
        # Local repository path
        repo_path = Path(repo_input).resolve()
        if not repo_path.is_dir():
            parser.error(f"Repository path does not exist: {repo_path}")
        project_name = args.project_name or repo_path.name

    logger.info(f"Running health checks on '{project_name}' at {repo_path}")

    static_analysis = get_static_analysis(repo_path)
    report = run_health_checks(static_analysis, project_name, repo_path=repo_path)

    report_path = output_dir / "health_report.json"
    report_path.write_text(report.model_dump_json(indent=2, exclude_none=True))

    logger.info(f"Health report written to {report_path} (overall score: {report.overall_score:.3f})")


if __name__ == "__main__":
    main()
