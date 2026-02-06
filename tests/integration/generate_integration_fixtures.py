#!/usr/bin/env python3
"""Generate fixture files for integration tests.

This script clones repositories at pinned commits, runs static analysis,
and saves the metrics to JSON fixture files for use in integration tests.

Usage:
    # Generate fixture for a specific repository
    uv run python tests/integration/generate_integration_fixtures.py --repo codeboarding

    # Generate all fixtures
    uv run python tests/integration/generate_integration_fixtures.py --all

    # List available repositories
    uv run python tests/integration/generate_integration_fixtures.py --list
"""

import argparse
import json
import os
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from dotenv import load_dotenv

# Add project root to path for imports
PROJECT_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# Load .env file to get STATIC_ANALYSIS_CONFIG and other environment variables
load_dotenv(PROJECT_ROOT / ".env")

from git import Repo

from repo_utils import clone_repository
from static_analyzer import get_static_analysis
from tests.integration.conftest import (
    REPOSITORY_CONFIGS,
    RepositoryTestConfig,
    create_mock_scanner,
    extract_metrics,
    FIXTURE_DIR,
)
from vscode_constants import update_config


def generate_fixture(config: RepositoryTestConfig, verbose: bool = True) -> dict:
    """Generate a fixture for a specific repository.

    Args:
        config: Repository test configuration
        verbose: Whether to print progress messages

    Returns:
        Fixture dictionary with metadata, metrics, and sample entities
    """
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)
        repo_root = tmp_path / "repos"
        repo_root.mkdir()
        cache_dir = tmp_path / "cache"
        cache_dir.mkdir()

        if verbose:
            print(f"  Cloning {config.repo_url}...")

        # Clone and checkout
        repo_name = clone_repository(config.repo_url, repo_root)
        repo_path = (repo_root / repo_name).resolve()
        repo = Repo(repo_path)

        if verbose:
            print(f"  Checking out {config.pinned_commit}...")
        repo.git.checkout(config.pinned_commit)

        if verbose:
            print(f"  Running static analysis for {config.language}...")

        # Run analysis with timing
        mock_scan = create_mock_scanner(config.mock_language)
        start_time = time.perf_counter()
        with patch("static_analyzer.scanner.ProjectScanner.scan", mock_scan):
            static_analysis = get_static_analysis(repo_path, cache_dir=cache_dir)
        end_time = time.perf_counter()
        execution_time = end_time - start_time

        # Extract metrics
        metrics = extract_metrics(static_analysis, config.language)
        metrics["execution_time_seconds"] = execution_time

        if verbose:
            print(f"  Metrics: {metrics}")
            print(f"  Execution time: {execution_time:.2f} seconds")

        # Extract sample entities (first 10 of each type)
        references = static_analysis.results.get(config.language, {}).get("references", {})
        sample_refs = sorted(list(references.keys()))[:10]

        try:
            hierarchy = static_analysis.get_hierarchy(config.language)
            sample_classes = sorted(list(hierarchy.keys()))[:10]
        except ValueError:
            sample_classes = []

        return {
            "metadata": {
                "repo_url": config.repo_url,
                "pinned_commit": config.pinned_commit,
                "language": config.language,
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "codeboarding_version": "0.2.0",
            },
            "metrics": metrics,
            "sample_references": sample_refs,
            "sample_classes": sample_classes,
        }


def save_fixture(config: RepositoryTestConfig, fixture: dict, verbose: bool = True) -> Path:
    """Save fixture to file.

    Args:
        config: Repository test configuration
        fixture: Fixture dictionary to save
        verbose: Whether to print progress messages

    Returns:
        Path to the saved fixture file
    """
    FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
    output_path = FIXTURE_DIR / config.fixture_file
    with open(output_path, "w") as f:
        json.dump(fixture, f, indent=2)
    if verbose:
        print(f"  Saved to {output_path}")
    return output_path


def main():
    parser = argparse.ArgumentParser(
        description="Generate integration test fixtures",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    # Generate fixture for CodeBoarding Python
    uv run python tests/integration/generate_integration_fixtures.py --repo codeboarding

    # Generate all fixtures
    uv run python tests/integration/generate_integration_fixtures.py --all

    # List available repositories
    uv run python tests/integration/generate_integration_fixtures.py --list
""",
    )
    parser.add_argument(
        "--repo",
        help="Repository name (partial match, e.g., 'codeboarding', 'mockito')",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Generate all fixtures",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="List available repositories",
    )
    parser.add_argument(
        "--quiet",
        "-q",
        action="store_true",
        help="Suppress progress messages",
    )
    parser.add_argument(
        "--binary-location",
        type=Path,
        default=PROJECT_ROOT / "static_analyzer" / "servers",
        help="Path to the binary directory for language servers and tools (default: static_analyzer/servers)",
    )
    args = parser.parse_args()

    verbose = not args.quiet

    # Update config with the binary location to ensure LSP servers are found
    if args.binary_location:
        update_config(args.binary_location)

    if args.list:
        print("Available repositories:")
        for config in REPOSITORY_CONFIGS:
            print(f"  {config.name}: {config.repo_url} ({config.language})")
        return

    if args.all:
        print("Generating all fixtures...")
        for config in REPOSITORY_CONFIGS:
            print(f"\nGenerating fixture for {config.name} ({config.language})...")
            try:
                fixture = generate_fixture(config, verbose)
                save_fixture(config, fixture, verbose)
            except Exception as e:
                print(f"  ERROR: {e}")
                if verbose:
                    import traceback

                    traceback.print_exc()
        print("\nDone!")

    elif args.repo:
        # Find matching config (case-insensitive partial match)
        matching = [c for c in REPOSITORY_CONFIGS if args.repo.lower() in c.name.lower()]
        if not matching:
            print(f"No repository matching '{args.repo}' found.")
            print("Available repositories:")
            for config in REPOSITORY_CONFIGS:
                print(f"  {config.name}")
            sys.exit(1)
        if len(matching) > 1:
            print(f"Multiple repositories match '{args.repo}':")
            for config in matching:
                print(f"  {config.name}")
            print("Please be more specific.")
            sys.exit(1)

        config = matching[0]
        print(f"Generating fixture for {config.name} ({config.language})...")
        fixture = generate_fixture(config, verbose)
        save_fixture(config, fixture, verbose)
        print("Done!")

    else:
        parser.print_help()


if __name__ == "__main__":
    main()
