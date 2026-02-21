import logging
import os
import re
import shutil
from pathlib import Path

import uuid
import yaml

from vscode_constants import VSCODE_CONFIG

logger = logging.getLogger(__name__)


class CFGGenerationError(Exception):
    pass


def create_temp_repo_folder():
    unique_id = uuid.uuid4().hex
    temp_dir = os.path.join("temp", unique_id)
    os.makedirs(temp_dir, exist_ok=False)
    return Path(temp_dir)


def remove_temp_repo_folder(temp_path: str):
    p = Path(temp_path)
    if not p.parts or p.parts[0] != "temp":
        raise ValueError(f"Refusing to delete outside of 'temp/': {temp_path!r}")
    shutil.rmtree(temp_path)


def caching_enabled():
    print("Caching enabled:", os.getenv("CACHING_DOCUMENTATION", "false"))
    return os.getenv("CACHING_DOCUMENTATION", "false").lower() in ("1", "true", "yes")


def get_cache_dir(repo_dir: Path) -> Path:
    return repo_dir / ".codeboarding" / "cache"


def get_project_root() -> Path:
    project_root_env = os.getenv("PROJECT_ROOT")
    if project_root_env:
        return Path(project_root_env)

    return Path(__file__).resolve().parent


def monitoring_enabled():
    print("Monitoring enabled:", os.getenv("ENABLE_MONITORING", "false"))
    return os.getenv("ENABLE_MONITORING", "false").lower() in ("1", "true", "yes")


_config_override: dict | None = None


def set_config(config: dict) -> None:
    """Programmatically set the tool/LSP configuration.

    When set, get_config() uses this dict instead of reading
    STATIC_ANALYSIS_CONFIG or falling back to VSCODE_CONFIG.
    """
    global _config_override
    _config_override = config


def clear_config() -> None:
    """Clear the programmatic config override."""
    global _config_override
    _config_override = None


def get_config(item_key: str):
    if _config_override is not None:
        if item_key not in _config_override:
            raise KeyError(f"Item '{item_key}' not found in configuration.")
        return _config_override[item_key]

    path = os.getenv("STATIC_ANALYSIS_CONFIG")
    if not path:
        logger.warning("STATIC_ANALYSIS_CONFIG environment variable is not set, using default VSCode Setup.")
        return default_config(item_key)
    config_path = Path(path)
    if not config_path.exists():
        raise FileNotFoundError(f"Configuration file not found at {config_path}")

    with open(config_path, "r") as file:
        config = yaml.safe_load(file)
    if item_key not in config:
        raise KeyError(f"Item '{item_key}' not found in configuration.")
    return config[item_key]


def default_config(item_key: str):
    return VSCODE_CONFIG.get(item_key)


def sanitize(name: str) -> str:
    """Replace non-alphanumerics with underscores so IDs are valid identifiers."""
    return re.sub(r"\W+", "_", name)
