#!/usr/bin/env python3
"""Diagnose static analysis edge distribution without running LLMs.

Usage:
    uv run python tests/static_analyzer/scripts/diagnose_relations.py <repo_path>

This script:
1. Runs static analysis (LSP) on the given repo
2. Classifies all CFG edges by file/package/directory
3. Creates mock components (one per top-level directory) and reports
   how many edges would become cross-component relations
4. Helps iterate on CFG quality without the LLM pipeline
"""

import argparse
import logging
import sys
from collections import defaultdict
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from static_analyzer import get_static_analysis
from static_analyzer.cluster_relations import build_component_relations
from static_analyzer.graph import CallGraph

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)


def classify_edges(cfg: CallGraph) -> dict:
    """Classify all edges by their source/destination relationship."""
    stats = {
        "total": 0,
        "same_file": 0,
        "same_package": 0,
        "cross_package": 0,
        "unknown_src": 0,
        "unknown_dst": 0,
    }
    cross_package_edges: list[tuple[str, str]] = []
    same_package_cross_file: list[tuple[str, str]] = []

    for edge in cfg.edges:
        stats["total"] += 1
        src = edge.get_source()
        dst = edge.get_destination()
        src_node = cfg.nodes.get(src)
        dst_node = cfg.nodes.get(dst)

        if not src_node:
            stats["unknown_src"] += 1
            continue
        if not dst_node:
            stats["unknown_dst"] += 1
            continue

        src_file = src_node.file_path
        dst_file = dst_node.file_path

        if src_file == dst_file:
            stats["same_file"] += 1
        else:
            src_pkg = _extract_package(src)
            dst_pkg = _extract_package(dst)
            if src_pkg == dst_pkg:
                stats["same_package"] += 1
                same_package_cross_file.append((src, dst))
            else:
                stats["cross_package"] += 1
                cross_package_edges.append((src, dst))

    return {
        "stats": stats,
        "cross_package_edges": cross_package_edges,
        "same_package_cross_file": same_package_cross_file,
    }


def build_directory_components(cfg: CallGraph, repo_path: Path) -> dict[str, str]:
    """Build a node->component map using top-level directories as components.

    Each top-level directory becomes a component. Files at the root become
    the "root" component.
    """
    node_to_component: dict[str, str] = {}
    for node in cfg.nodes.values():
        try:
            rel = Path(node.file_path).relative_to(repo_path)
            parts = rel.parts
            component = parts[0] if len(parts) > 1 else "root"
        except ValueError:
            component = "unknown"
        node_to_component[node.fully_qualified_name] = component
    return node_to_component


def analyze_component_coverage(cfg: CallGraph, node_to_component: dict[str, str]) -> dict:
    """Analyze how many edges map to components and which are lost."""
    mapped_both = 0
    mapped_src_only = 0
    mapped_dst_only = 0
    mapped_neither = 0
    intra_component = 0
    cross_component = 0
    cross_component_pairs: dict[tuple[str, str], list[tuple[str, str]]] = defaultdict(list)
    unmapped_sample: list[tuple[str, str, str]] = []  # (src, dst, reason)

    for edge in cfg.edges:
        src = edge.get_source()
        dst = edge.get_destination()
        src_comp = node_to_component.get(src)
        dst_comp = node_to_component.get(dst)

        if src_comp and dst_comp:
            mapped_both += 1
            if src_comp == dst_comp:
                intra_component += 1
            else:
                cross_component += 1
                cross_component_pairs[(src_comp, dst_comp)].append((src, dst))
        elif src_comp and not dst_comp:
            mapped_src_only += 1
            if len(unmapped_sample) < 10:
                unmapped_sample.append((src, dst, "dst unmapped"))
        elif dst_comp and not src_comp:
            mapped_dst_only += 1
            if len(unmapped_sample) < 10:
                unmapped_sample.append((src, dst, "src unmapped"))
        else:
            mapped_neither += 1
            if len(unmapped_sample) < 10:
                unmapped_sample.append((src, dst, "both unmapped"))

    return {
        "mapped_both": mapped_both,
        "mapped_src_only": mapped_src_only,
        "mapped_dst_only": mapped_dst_only,
        "mapped_neither": mapped_neither,
        "intra_component": intra_component,
        "cross_component": cross_component,
        "cross_component_pairs": cross_component_pairs,
        "unmapped_sample": unmapped_sample,
    }


def _extract_package(qualified_name: str) -> str:
    """Extract package from a qualified name like 'agents.abstraction_agent.run' -> 'agents'."""
    parts = qualified_name.split(".")
    return parts[0] if parts else ""


def print_report(edge_info: dict, coverage: dict, node_count: int) -> None:
    """Print a human-readable diagnostic report."""
    stats = edge_info["stats"]

    print("\n" + "=" * 70)
    print("CFG EDGE DIAGNOSTIC REPORT")
    print("=" * 70)

    print(f"\nNodes: {node_count}")
    print(f"Total edges: {stats['total']}")
    print(f"  Same file:     {stats['same_file']:>5} ({stats['same_file']/max(stats['total'],1)*100:.1f}%)")
    print(f"  Same package:  {stats['same_package']:>5} ({stats['same_package']/max(stats['total'],1)*100:.1f}%)")
    print(f"  Cross package: {stats['cross_package']:>5} ({stats['cross_package']/max(stats['total'],1)*100:.1f}%)")
    if stats["unknown_src"] or stats["unknown_dst"]:
        print(f"  Unknown src:   {stats['unknown_src']:>5}")
        print(f"  Unknown dst:   {stats['unknown_dst']:>5}")

    print(f"\n--- Component Coverage (top-level directory = component) ---")
    print(f"  Both endpoints mapped:  {coverage['mapped_both']:>5}")
    print(f"    Intra-component:      {coverage['intra_component']:>5}")
    print(f"    Cross-component:      {coverage['cross_component']:>5}")
    print(f"  Only src mapped:        {coverage['mapped_src_only']:>5}")
    print(f"  Only dst mapped:        {coverage['mapped_dst_only']:>5}")
    print(f"  Neither mapped:         {coverage['mapped_neither']:>5}")

    if coverage["cross_component_pairs"]:
        print(f"\n--- Cross-Component Relations ({len(coverage['cross_component_pairs'])} pairs) ---")
        for (src_comp, dst_comp), edges in sorted(
            coverage["cross_component_pairs"].items(),
            key=lambda x: -len(x[1]),
        ):
            print(f"  {src_comp} -> {dst_comp}: {len(edges)} edges")
            for src, dst in edges[:3]:
                print(f"    e.g. {src} -> {dst}")

    if edge_info["cross_package_edges"]:
        print(f"\n--- Sample Cross-Package Edges ({len(edge_info['cross_package_edges'])} total) ---")
        for src, dst in edge_info["cross_package_edges"][:15]:
            print(f"  {src} -> {dst}")

    if coverage["unmapped_sample"]:
        print(f"\n--- Sample Unmapped Edges ---")
        for src, dst, reason in coverage["unmapped_sample"]:
            print(f"  [{reason}] {src} -> {dst}")

    print("\n" + "=" * 70)


def main():
    parser = argparse.ArgumentParser(description="Diagnose static analysis edge distribution")
    parser.add_argument("repo_path", type=Path, help="Path to the repository to analyze")
    parser.add_argument("--skip-cache", action="store_true", help="Skip analysis cache")
    args = parser.parse_args()

    repo_path = args.repo_path.resolve()
    if not repo_path.exists():
        print(f"Error: {repo_path} does not exist")
        sys.exit(1)

    print(f"Running static analysis on {repo_path}...")
    results = get_static_analysis(repo_path, skip_cache=args.skip_cache)

    for language in results.get_languages():
        print(f"\n{'#' * 70}")
        print(f"# Language: {language}")
        print(f"{'#' * 70}")

        cfg = results.get_cfg(language)
        edge_info = classify_edges(cfg)
        node_to_component = build_directory_components(cfg, repo_path)
        coverage = analyze_component_coverage(cfg, node_to_component)

        # Also run the actual build_component_relations
        relations = build_component_relations(node_to_component, {language: cfg})

        print_report(edge_info, coverage, len(cfg.nodes))

        print(f"\n--- build_component_relations result: {len(relations)} relations ---")
        for rel in sorted(relations, key=lambda r: -r.edge_count):
            print(f"  {rel.src_cluster_id} -> {rel.dst_cluster_id}: {rel.edge_count} edges")
            for src, dst in rel.sample_edges[:2]:
                print(f"    e.g. {src} -> {dst}")


if __name__ == "__main__":
    main()
