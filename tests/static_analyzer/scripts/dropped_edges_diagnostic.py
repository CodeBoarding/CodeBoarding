"""Diagnostic script to analyze dropped edges from CFG construction."""

import logging
from collections import Counter
from pathlib import Path

dropped_edges: list[tuple[str, str, bool, bool]] = []

from static_analyzer.graph import CallGraph

original_add_edge = CallGraph.add_edge


def patched_add_edge(self, src_name, dst_name):
    if src_name not in self.nodes or dst_name not in self.nodes:
        src_missing = src_name not in self.nodes
        dst_missing = dst_name not in self.nodes
        dropped_edges.append((src_name, dst_name, src_missing, dst_missing))
        return
    return original_add_edge(self, src_name, dst_name)


CallGraph.add_edge = patched_add_edge

logging.basicConfig(level=logging.WARNING)

from static_analyzer import StaticAnalyzer

analyzer = StaticAnalyzer(Path("."))
analyzer.start_clients()
try:
    results = analyzer.analyze()
finally:
    analyzer.stop_clients()


def get_module(name):
    parts = name.rsplit(".", 1)
    return parts[0] if len(parts) > 1 else ""


both_missing = [(s, d) for s, d, sm, dm in dropped_edges if sm and dm]
src_only_missing = [(s, d) for s, d, sm, dm in dropped_edges if sm and not dm]
dst_only_missing = [(s, d) for s, d, sm, dm in dropped_edges if not sm and dm]

print(f"Total dropped edges: {len(dropped_edges)}")
print(f"  Both missing: {len(both_missing)}")
print(f"  Src only missing: {len(src_only_missing)}")
print(f"  Dst only missing: {len(dst_only_missing)}")

print()
print("=== Dst-only missing, cross-module (first 30 unique) ===")
cross_dst = sorted(set((s, d) for s, d in dst_only_missing if get_module(s) != get_module(d)))
for s, d in cross_dst[:30]:
    print(f"  {s} -> {d}")
print(f"Total unique cross-module (dst missing): {len(cross_dst)}")

print()
print("=== Src-only missing, cross-module (first 30 unique) ===")
cross_src = sorted(set((s, d) for s, d in src_only_missing if get_module(s) != get_module(d)))
for s, d in cross_src[:30]:
    print(f"  {s} -> {d}")
print(f"Total unique cross-module (src missing): {len(cross_src)}")

print()
print("=== Most common missing dst names ===")
missing_dsts = Counter(d for _, d in dst_only_missing)
for name, cnt in missing_dsts.most_common(20):
    print(f"  {cnt:4d}x  {name}")

print()
print("=== Most common missing src names ===")
missing_srcs = Counter(s for s, _ in src_only_missing)
for name, cnt in missing_srcs.most_common(20):
    print(f"  {cnt:4d}x  {name}")

print()
print("=== Both-missing, cross-module (first 20 unique) ===")
cross_both = sorted(set((s, d) for s, d in both_missing if get_module(s) != get_module(d)))
for s, d in cross_both[:20]:
    print(f"  {s} -> {d}")
print(f"Total unique cross-module (both missing): {len(cross_both)}")
