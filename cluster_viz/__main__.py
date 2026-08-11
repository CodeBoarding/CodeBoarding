"""CLI: serialize a finished analysis' clustering and render the viewer.

python -m cluster_viz --artifacts runs/cluster-viz/eshop --repo /path/to/eShop
"""

import argparse
import json
import logging
from pathlib import Path

from cluster_viz.export import export_clustering
from cluster_viz.render import render_html

logger = logging.getLogger(__name__)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cluster_viz",
        description="Serialize the call graph and every clustering level of a finished analysis.",
    )
    parser.add_argument("--artifacts", type=Path, required=True, help="Directory holding analysis.json + the pkl")
    parser.add_argument("--repo", type=Path, required=True, help="Repository the analysis was run on")
    parser.add_argument("--json", type=Path, help="Where to write the payload (default: <artifacts>/clustering.json)")
    parser.add_argument("--html", type=Path, help="Where to write the viewer (default: <artifacts>/clustering.html)")
    return parser


def main(argv: list[str] | None = None) -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    args = build_parser().parse_args(argv)

    payload = export_clustering(args.artifacts, args.repo)
    json_path = args.json or args.artifacts / "clustering.json"
    html_path = args.html or args.artifacts / "clustering.html"
    json_path.write_text(json.dumps(payload, indent=1), encoding="utf-8")
    html_path.write_text(render_html(payload), encoding="utf-8")

    meta = payload["meta"]
    logger.info(
        "%s: %d methods, %d edges, %d levels, %d clustering scopes",
        meta["project"],
        meta["node_count"],
        meta["edge_count"],
        meta["levels"],
        len(payload["scopes"]),
    )
    for warning in meta["warnings"]:
        logger.warning("warning: %s", warning)
    logger.info("wrote %s", json_path)
    logger.info("wrote %s", html_path)


if __name__ == "__main__":
    main()
