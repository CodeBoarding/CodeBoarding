"""Health check configuration management.

Provides utilities for loading health check configurations from project files,
similar to how CodeBoarding ignore patterns are managed.
"""

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

HEALTHIGNORE_TEMPLATE = """# Health Check Exclusion Patterns
# Add patterns here for entities that should be excluded from specific health checks.
# Use fnmatch glob syntax (same as shell wildcards).
#
# Examples:
# - Exclude all functions in evals: evals.*
# - Exclude a specific function: utils.get_project_root
# - Exclude by file path: */evals/*
# - Exclude functions matching a pattern: *._*
#
# This file is automatically loaded by health checks to exclude specified
# entities from analysis and reporting.
"""


def load_health_exclude_patterns(health_config_dir: Path | None = None) -> list[str]:
    """Load orphan code exclusion patterns from .healthignore file.

    Args:
        health_config_dir: Path to the health config directory (typically .codeboarding/health).
                          If None, will search relative to current working directory.

    Returns:
        List of exclusion patterns (fnmatch glob patterns).
    """
    if health_config_dir is None:
        # Try to find .codeboarding/health directory
        cwd = Path.cwd()
        health_config_dir = cwd / ".codeboarding" / "health"
        if not health_config_dir.exists():
            logger.debug("No .codeboarding/health directory found")
            return []

    healthignore_path = health_config_dir / ".healthignore"
    patterns = []

    if healthignore_path.exists():
        try:
            with healthignore_path.open("r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    # Skip empty lines and comments
                    if line and not line.startswith("#"):
                        patterns.append(line)
            logger.debug(f"Loaded {len(patterns)} health exclusion patterns from {healthignore_path}")
        except Exception as e:
            logger.warning(f"Failed to read .healthignore at {healthignore_path}: {e}")
    else:
        logger.debug(f"No .healthignore file found at {healthignore_path}")

    return patterns


def initialize_healthignore(health_config_dir: Path) -> None:
    """Initialize .healthignore file in the health config directory if it doesn't exist.

    Args:
        health_config_dir: Path to the health config directory (typically .codeboarding/health).
    """
    # Ensure the health config directory exists
    health_config_dir.mkdir(parents=True, exist_ok=True)

    healthignore_path = health_config_dir / ".healthignore"

    if not healthignore_path.exists():
        try:
            healthignore_path.write_text(HEALTHIGNORE_TEMPLATE, encoding="utf-8")
            logger.debug(f"Created .healthignore file at {healthignore_path}")
        except Exception as e:
            logger.warning(f"Failed to create .healthignore at {healthignore_path}: {e}")
