"""Health check configuration management.

Provides utilities for loading health check configurations from project files,
similar to how CodeBoarding ignore patterns are managed.
"""

import json
import logging
from pathlib import Path

from health.models import HealthCheckConfig

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

HEALTH_CONFIG_TEMPLATE = json.dumps(
    {
        "function_size_max": {
            "description": "Maximum lines of code allowed in a single function before it is flagged. Range: 50-500.",
            "value": 150,
        },
        "fan_out_max": {
            "description": "Maximum number of outgoing calls from a function. High fan-out indicates a function depends on too many others. Range: 5-30.",
            "value": 10,
        },
        "fan_in_max": {
            "description": "Maximum number of incoming calls to a function. High fan-in means many functions depend on this one, making changes risky. Range: 5-50.",
            "value": 10,
        },
        "god_class_method_count_max": {
            "description": "Maximum number of methods a class can have before being flagged as a God Class. Range: 10-50.",
            "value": 25,
        },
        "god_class_loc_max": {
            "description": "Maximum lines of code in a class before being flagged as a God Class. Range: 200-1000.",
            "value": 400,
        },
        "god_class_fan_out_max": {
            "description": "Maximum outgoing dependencies from a class before being flagged as a God Class. Range: 10-60.",
            "value": 30,
        },
        "inheritance_depth_max": {
            "description": "Maximum depth of class inheritance hierarchy. Deep hierarchies are harder to understand. Range: 3-10.",
            "value": 5,
        },
        "max_cycles_reported": {
            "description": "Maximum number of circular dependency cycles to include in the report. Range: 10-200.",
            "value": 50,
        },
        "instability_high": {
            "description": "Package instability threshold (0.0 = fully stable, 1.0 = fully unstable). Packages above this value are flagged. Range: 0.5-1.0.",
            "value": 0.8,
        },
        "cohesion_low": {
            "description": "Low cohesion threshold for components. Components below this value are flagged as having poor internal cohesion. Range: 0.0-0.5.",
            "value": 0.1,
        },
    },
    indent=4,
)


def _initialize_template(file_path: Path, template: str) -> None:
    """Write a template file if it doesn't already exist."""
    if not file_path.exists():
        try:
            file_path.write_text(template, encoding="utf-8")
            logger.debug(f"Created {file_path.name} at {file_path}")
        except Exception as e:
            logger.warning(f"Failed to create {file_path.name} at {file_path}: {e}")


def initialize_health_dir(health_config_dir: Path) -> None:
    """Initialize the health config directory with default config files.

    Creates .healthignore and health_config.json with default templates
    if they don't already exist. Idempotent — existing files are not overwritten.

    Args:
        health_config_dir: Path to the health config directory (typically .codeboarding/health).
    """
    health_config_dir.mkdir(parents=True, exist_ok=True)
    _initialize_template(health_config_dir / ".healthignore", HEALTHIGNORE_TEMPLATE)
    _initialize_template(health_config_dir / "health_config.json", HEALTH_CONFIG_TEMPLATE)


def _load_health_exclude_patterns(health_config_dir: Path) -> list[str]:
    """Load health check exclusion patterns from .healthignore file.

    Args:
        health_config_dir: Path to the health config directory.

    Returns:
        List of exclusion patterns (fnmatch glob patterns).
    """
    healthignore_path = health_config_dir / ".healthignore"
    patterns = []

    if healthignore_path.exists():
        try:
            with healthignore_path.open("r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#"):
                        patterns.append(line)
            logger.debug(f"Loaded {len(patterns)} health exclusion patterns from {healthignore_path}")
        except Exception as e:
            logger.warning(f"Failed to read .healthignore at {healthignore_path}: {e}")
    else:
        logger.debug(f"No .healthignore file found at {healthignore_path}")

    return patterns


def load_health_config(health_config_dir: Path | None = None) -> HealthCheckConfig:
    """Load health check configuration from health_config.json.

    Reads threshold overrides from health_config.json and exclusion patterns from
    .healthignore, merging them into a single HealthCheckConfig. Falls back to
    default values if the config file is missing, malformed, or contains invalid values.

    Args:
        health_config_dir: Path to the health config directory (typically .codeboarding/health).
                          If None, will search relative to current working directory.

    Returns:
        A HealthCheckConfig with user overrides applied (or defaults on error).
    """
    if health_config_dir is None:
        cwd = Path.cwd()
        health_config_dir = cwd / ".codeboarding" / "health"
        if not health_config_dir.exists():
            logger.debug("No .codeboarding/health directory found")
            return HealthCheckConfig()

    exclude_patterns = _load_health_exclude_patterns(health_config_dir)
    config_path = health_config_dir / "health_config.json"

    if not config_path.exists():
        logger.debug(f"No health_config.json found at {config_path}, using defaults")
        return HealthCheckConfig(health_exclude_patterns=exclude_patterns)

    try:
        raw = config_path.read_text(encoding="utf-8")
        data = json.loads(raw)
        # Extract "value" from each {description, value} object
        flat: dict[str, int | float] = {}
        for key, entry in data.items():
            if isinstance(entry, dict) and "value" in entry:
                flat[key] = entry["value"]
            else:
                flat[key] = entry
        config = HealthCheckConfig.model_validate(flat)
        config.health_exclude_patterns = exclude_patterns
        logger.debug(f"Loaded health config from {config_path}")
        return config
    except Exception as e:
        logger.warning(f"Failed to parse health_config.json at {config_path}: {e}. Using default configuration.")
        return HealthCheckConfig(health_exclude_patterns=exclude_patterns)
