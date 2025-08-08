import os
import shutil
from pathlib import Path

import uuid
import yaml


class CFGGenerationError(Exception):
    pass


def create_temp_repo_folder():
    unique_id = uuid.uuid4().hex
    temp_dir = os.path.join('temp', unique_id)
    os.makedirs(temp_dir, exist_ok=False)
    return Path(temp_dir)


def remove_temp_repo_folder(temp_path: str):
    p = Path(temp_path)
    if not p.parts or p.parts[0] != "temp":
        raise ValueError(f"Refusing to delete outside of 'temp/': {temp_path!r}")
    shutil.rmtree(temp_path)


def caching_enabled():
    print("Caching enabled:", os.getenv('CACHING_DOCUMENTATION', 'false'))
    return os.getenv('CACHING_DOCUMENTATION', 'false').lower() in ('1', 'true', 'yes')


def contains_json(node_id, files):
    for file in files:
        if str(file).endswith(f"{node_id}.json"):
            return True
    return False


def get_config(item_key: str):
    path = os.getenv("STATIC_ANALYSIS_CONFIG")
    if not path:
        raise ValueError("STATIC_ANALYSIS_CONFIG environment variable is not set.")
    config_path = Path(path)
    if not config_path.exists():
        raise FileNotFoundError(f"Configuration file not found at {config_path}")

    with open(config_path, 'r') as file:
        config = yaml.safe_load(file)
    if item_key not in config:
        raise KeyError(f"Item '{item_key}' not found in configuration.")
    return config[item_key]
