from __future__ import annotations

import argparse
import sys
from pathlib import Path

from static_analyzer.pylint_analyze import _banner
from static_analyzer.pylint_analyze.call_graph_builder import CallGraphBuilder


# ─────────────────────────────  CLI wiring  ────────────────────────────────
def parse_cli() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Static structure or call‑graph generator using pylint/astroid."
    )
    p.add_argument("path", help="Package / directory / module to analyse")
    p.add_argument(
        "-m",
        "--mode",
        choices=("structure", "callgraph"),
        default="structure",
        help="Type of graph to generate",
    )
    p.add_argument(
        "-o",
        "--output",
        help="Output file (.dot or .json).  "
        "Default: <root>_<mode>.dot (or .json if extension is .json)",
    )
    p.add_argument("--depth", type=int, help="Maximum dir depth (callgraph mode only)")
    p.add_argument("-v", "--verbose", action="store_true")
    return p.parse_args()


def main():
    ns = parse_cli()
    root_path = Path(ns.path).expanduser().resolve()
    if not root_path.exists():
        sys.exit(f"Path does not exist: {root_path}")

    default_dot = root_path.name + f"_{ns.mode}.dot"
    out_file = Path(ns.output) if ns.output else Path(default_dot)

    if ns.mode == "structure":
        from static_analyzer.pylint_analyze.structure_graph_builder import StructureGraphBuilder
        graph_builder = StructureGraphBuilder(root_path, out_file, verbose=ns.verbose)
        graph_builder.build()
    else:  # callgraph
        builder = CallGraphBuilder(root_path, max_depth=ns.depth, verbose=ns.verbose)
        graph = builder.build()
        if out_file.suffix == ".json":
            builder.write_json(out_file)
        else:
            builder.write_dot(out_file)
        _banner(f"Call‑graph written to {out_file}", ns.verbose)


if __name__ == "__main__":
    main()