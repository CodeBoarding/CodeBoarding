"""`codeboarding serve` — local web visualizer with live progress streaming."""

import argparse
import logging
import threading
import webbrowser

import uvicorn

from codeboarding_cli.bootstrap import bootstrap_environment, resolve_local_run_paths
from codeboarding_web import create_app

logger = logging.getLogger(__name__)


def add_arguments(subparsers: argparse._SubParsersAction, parents: list[argparse.ArgumentParser]) -> None:
    """Register the serve subparser and its arguments."""
    parser = subparsers.add_parser(
        "serve",
        parents=parents,
        help="Serve an interactive, live-updating diagram for a local repository.",
    )
    parser.add_argument("--host", default="127.0.0.1", help="Host to bind (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8050, help="Port to bind (default: 8050)")
    parser.add_argument("--no-open", action="store_true", help="Do not open a browser tab")
    parser.add_argument(
        "--depth-level",
        type=int,
        default=2,
        help="Analysis depth (default: 2). Depth >=2 generates nested sub-components that can be expanded in the diagram; use 1 for a flat top-level-only view.",
    )
    parser.add_argument(
        "--watch",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Auto re-analyze on source changes (default: on)",
    )


def run_from_args(args: argparse.Namespace, parser: argparse.ArgumentParser) -> None:
    """Start the web visualizer from parsed CLI arguments."""
    if args.local is None:
        parser.error("serve requires --local <path>")
    run_paths = resolve_local_run_paths(args)
    run_paths.output_dir.mkdir(parents=True, exist_ok=True)
    bootstrap_environment(run_paths.output_dir, args.binary_location)

    app = create_app(
        repo_path=run_paths.repo_path,
        output_dir=run_paths.output_dir,
        project_name=run_paths.project_name,
        depth_level=args.depth_level,
        watch=args.watch,
    )

    url = f"http://{args.host}:{args.port}/"
    if not args.no_open:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    logger.info("Serving CodeBoarding visualizer at %s", url)
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")
