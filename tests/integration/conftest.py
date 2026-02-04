"""Shared fixtures and utilities for static analysis integration tests.

This module provides:
- RepositoryTestConfig dataclass for test configuration
- Mock scanner factory for ProjectScanner.scan()
- Fixture loading and metrics extraction utilities
- Repository configurations for all supported languages
"""

import json
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Generator

import pytest

from static_analyzer.programming_language import ProgrammingLanguage, JavaConfig
from utils import get_config

FIXTURE_DIR = Path(__file__).parent / "fixtures"


@dataclass(frozen=True)
class RepositoryTestConfig:
    """Configuration for a repository integration test."""

    name: str
    repo_url: str
    pinned_commit: str
    language: str
    fixture_file: str
    mock_language: dict


# Repository configurations for all supported languages
# Using stable release tags for reproducibility
REPOSITORY_CONFIGS = [
    RepositoryTestConfig(
        name="codeboarding_python",
        repo_url="https://github.com/CodeBoarding/CodeBoarding",
        pinned_commit="03b25afe8d37ce733e5f70c3cbcdfb52f4883dcd",
        language="Python",
        fixture_file="codeboarding_python.json",
        mock_language={
            "language": "Python",
            "size": 50000,
            "percentage": 100.0,
            "suffixes": [".py"],
            "server_commands": ["pyright-langserver", "--stdio"],
            "lsp_server_key": "python",
        },
    ),
    RepositoryTestConfig(
        name="mockito_java",
        repo_url="https://github.com/mockito/mockito",
        pinned_commit="v5.14.2",
        language="Java",
        fixture_file="mockito_java.json",
        mock_language={
            "language": "Java",
            "size": 100000,
            "percentage": 100.0,
            "suffixes": [".java"],
            "server_commands": ["java"],
            "lsp_server_key": "java",
        },
    ),
    RepositoryTestConfig(
        name="prometheus_go",
        repo_url="https://github.com/prometheus/prometheus",
        pinned_commit="v3.0.1",
        language="Go",
        fixture_file="prometheus_go.json",
        mock_language={
            "language": "Go",
            "size": 200000,
            "percentage": 100.0,
            "suffixes": [".go"],
            "server_commands": ["gopls", "serve"],
            "lsp_server_key": "go",
        },
    ),
    RepositoryTestConfig(
        name="excalidraw_typescript",
        repo_url="https://github.com/excalidraw/excalidraw",
        pinned_commit="v0.18.0",
        language="TypeScript",
        fixture_file="excalidraw_typescript.json",
        mock_language={
            "language": "TypeScript",
            "size": 150000,
            "percentage": 100.0,
            "suffixes": [".ts", ".tsx"],
            "server_commands": ["typescript-language-server", "--stdio"],
            "lsp_server_key": "typescript",
        },
    ),
    RepositoryTestConfig(
        name="wordpress_php",
        repo_url="https://github.com/WordPress/WordPress",
        pinned_commit="6.7",
        language="PHP",
        fixture_file="wordpress_php.json",
        mock_language={
            "language": "PHP",
            "size": 300000,
            "percentage": 100.0,
            "suffixes": [".php"],
            "server_commands": ["intelephense", "--stdio"],
            "lsp_server_key": "php",
        },
    ),
    RepositoryTestConfig(
        name="lodash_javascript",
        repo_url="https://github.com/lodash/lodash",
        pinned_commit="4.17.21",
        language="JavaScript",
        fixture_file="lodash_javascript.json",
        mock_language={
            "language": "JavaScript",
            "size": 100000,
            "percentage": 100.0,
            "suffixes": [".js", ".mjs"],
            "server_commands": ["typescript-language-server", "--stdio"],
            "lsp_server_key": "typescript",
        },
    ),
]


def create_mock_scanner(mock_language: dict):
    """Create a mock function for ProjectScanner.scan().

    Args:
        mock_language: Dictionary with language configuration

    Returns:
        A function that returns a list with a single ProgrammingLanguage
    """

    def _mock_scan(self) -> list[ProgrammingLanguage]:
        # Load the actual LSP server config to get language-specific configs and server commands
        lsp_servers = get_config("lsp_servers")
        lsp_server_key = mock_language["lsp_server_key"]

        # Get server commands and language-specific config from the actual LSP config
        server_commands = None
        language_specific_config = None

        if lsp_server_key in lsp_servers:
            config = lsp_servers[lsp_server_key]
            server_commands = config.get("command")

            # Create language-specific config if needed (e.g., JavaConfig with jdtls_root)
            if lsp_server_key == "java" and "jdtls_root" in config:
                language_specific_config = JavaConfig(jdtls_root=Path(config["jdtls_root"]))

        return [
            ProgrammingLanguage(
                language=mock_language["language"],
                size=mock_language["size"],
                percentage=mock_language["percentage"],
                suffixes=mock_language["suffixes"],
                server_commands=server_commands or mock_language["server_commands"],
                lsp_server_key=lsp_server_key,
                language_specific_config=language_specific_config,
            )
        ]

    return _mock_scan


def load_fixture(fixture_filename: str) -> dict:
    """Load a fixture file from the fixtures directory.

    Args:
        fixture_filename: Name of the fixture file

    Returns:
        Parsed JSON content as a dictionary
    """
    fixture_path = FIXTURE_DIR / fixture_filename
    with open(fixture_path) as f:
        return json.load(f)


def extract_metrics(static_analysis, language: str) -> dict:
    """Extract comparable metrics from StaticAnalysisResults.

    Args:
        static_analysis: StaticAnalysisResults instance
        language: Language name to extract metrics for

    Returns:
        Dictionary with metric counts
    """
    try:
        cfg = static_analysis.get_cfg(language)
        nodes_count = len(cfg.nodes)
        edges_count = len(cfg.edges)
    except ValueError:
        nodes_count = 0
        edges_count = 0

    try:
        references = static_analysis.results.get(language, {}).get("references", {})
        references_count = len(references)
    except (KeyError, AttributeError):
        references_count = 0

    try:
        hierarchy = static_analysis.get_hierarchy(language)
        classes_count = len(hierarchy)
    except ValueError:
        classes_count = 0

    try:
        packages = static_analysis.get_package_dependencies(language)
        packages_count = len(packages)
    except ValueError:
        packages_count = 0

    try:
        source_files = static_analysis.get_source_files(language)
        source_files_count = len(source_files)
    except (ValueError, KeyError):
        source_files_count = 0

    return {
        "references_count": references_count,
        "classes_count": classes_count,
        "packages_count": packages_count,
        "call_graph_nodes": nodes_count,
        "call_graph_edges": edges_count,
        "source_files_count": source_files_count,
    }


@pytest.fixture(scope="function")
def temp_workspace() -> Generator[Path, None, None]:
    """Provide a temporary directory for test isolation."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        yield Path(tmp_dir)
