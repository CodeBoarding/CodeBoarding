"""CLI command for context management."""

import argparse
import logging
from pathlib import Path

from repo_utils.context_manager import (
    GLOBAL_CONTEXT,
    create_context,
    delete_context,
    get_current_context,
    list_contexts,
    save_context,
    set_context,
)

logger = logging.getLogger(__name__)


def add_arguments(subparsers: argparse._SubParsersAction, parents: list[argparse.ArgumentParser]) -> None:
    parser = subparsers.add_parser(
        "context",
        parents=parents,
        help="Manage analysis contexts for focused file sets",
    )
    context_subparsers = parser.add_subparsers(dest="context_command", required=True, metavar="ACTION")

    # list
    context_subparsers.add_parser("list", help="List saved contexts")

    # create
    create_parser = context_subparsers.add_parser("create", help="Create a new context")
    create_parser.add_argument("name", help="Context name")

    # set
    set_parser = context_subparsers.add_parser("set", help="Switch to a context")
    set_parser.add_argument("name", help="Context name (use 'global' for full repo)")

    # save
    save_parser = context_subparsers.add_parser("save", help="Save current analysis to a context")
    save_parser.add_argument("name", help="Context name")

    # delete
    delete_parser = context_subparsers.add_parser("delete", help="Delete a context")
    delete_parser.add_argument("name", help="Context name")


def validate_arguments(args: argparse.Namespace, parser: argparse.ArgumentParser) -> None:
    if args.local is None:
        parser.error("--local is required for context commands")


def run_from_args(args: argparse.Namespace, parser: argparse.ArgumentParser) -> None:
    validate_arguments(args, parser)
    repo_root = args.local.resolve()

    action = args.context_command
    if action == "list":
        _cmd_list(repo_root)
    elif action == "create":
        _cmd_create(repo_root, args.name)
    elif action == "set":
        _cmd_set(repo_root, args.name)
    elif action == "save":
        _cmd_save(repo_root, args.name)
    elif action == "delete":
        _cmd_delete(repo_root, args.name)


def _cmd_list(repo_root: Path) -> None:
    contexts = list_contexts(repo_root)
    current = get_current_context(repo_root)
    if not contexts:
        print("No contexts found.")
        return

    print("Saved contexts:")
    for name in contexts:
        marker = " (active)" if name == current else ""
        print(f"  {name}{marker}")


def _cmd_create(repo_root: Path, name: str) -> None:
    try:
        ctx_dir = create_context(repo_root, name)
        print(f"Created context '{name}' at {ctx_dir}")
        print(f"Edit {ctx_dir}/.codeboarding-include to add file patterns")
    except ValueError as e:
        logger.error(str(e))
        raise SystemExit(1) from e


def _cmd_set(repo_root: Path, name: str) -> None:
    try:
        set_context(repo_root, name)
        print(f"Switched to context '{name}'")
        if name == GLOBAL_CONTEXT:
            print("Running analysis on full repository (no .codeboarding-include)")
        else:
            print(f"Run 'codeboarding full --local {repo_root}' to analyze with this context")
    except ValueError as e:
        logger.error(str(e))
        raise SystemExit(1) from e


def _cmd_save(repo_root: Path, name: str) -> None:
    try:
        save_context(repo_root, name)
        print(f"Saved analysis to context '{name}'")
    except ValueError as e:
        logger.error(str(e))
        raise SystemExit(1) from e


def _cmd_delete(repo_root: Path, name: str) -> None:
    try:
        delete_context(repo_root, name)
        print(f"Deleted context '{name}'")
    except ValueError as e:
        logger.error(str(e))
        raise SystemExit(1) from e
