import argparse
import logging
from pathlib import Path

from agents.llm_config import configure_models, validate_api_key_provided
from core import get_registries, load_plugins
from install import ensure_tools
from logging_config import setup_logging
from tool_registry import needs_install
from user_config import ensure_config_template, load_user_config
from vscode_constants import update_config

logger = logging.getLogger(__name__)


def resolve_local_run_paths(args: argparse.Namespace) -> tuple[Path, Path, str]:
    """Derive the (repo_path, output_dir, project_name) triple shared by all local-mode commands.

    Pure: no I/O, no mkdir. Callers decide when to create the output directory,
    since ``bootstrap_environment`` vs ``mkdir`` ordering differs by command.
    """
    repo_path = args.local.resolve()
    output_dir = args.output_dir.resolve() if args.output_dir else repo_path / ".codeboarding"
    project_name = args.project_name or repo_path.name
    return repo_path, output_dir, project_name


def bootstrap_environment(output_dir: Path, binary_location: Path | None) -> None:
    setup_logging(log_dir=output_dir)
    ensure_config_template()
    user_cfg = load_user_config()
    user_cfg.apply_to_env()
    configure_models(agent_model=user_cfg.llm.agent_model, parsing_model=user_cfg.llm.parsing_model)
    validate_api_key_provided()
    load_plugins(get_registries())
    if binary_location is not None:
        update_config(binary_location)
    elif needs_install():
        logger.info("First run: downloading language server binaries to ~/.codeboarding/servers/ ...")
        ensure_tools(auto_install_npm=True, auto_install_vcpp=True)
