"""Diagnostic script to identify recoverable dropped edges."""

import logging
from collections import Counter, defaultdict
from pathlib import Path

dropped_edges: list[tuple[str, str, bool, bool]] = []
all_node_names: set[str] = set()

from static_analyzer.graph import CallGraph

original_add_edge = CallGraph.add_edge
original_add_node = CallGraph.add_node


def patched_add_edge(self, src_name, dst_name):
    if src_name not in self.nodes or dst_name not in self.nodes:
        src_missing = src_name not in self.nodes
        dst_missing = dst_name not in self.nodes
        dropped_edges.append((src_name, dst_name, src_missing, dst_missing))
        return
    return original_add_edge(self, src_name, dst_name)


def patched_add_node(self, node):
    all_node_names.add(node.fully_qualified_name)
    return original_add_node(self, node)


CallGraph.add_edge = patched_add_edge
CallGraph.add_node = patched_add_node

logging.basicConfig(level=logging.WARNING)

from static_analyzer import StaticAnalyzer

analyzer = StaticAnalyzer(Path("."))
analyzer.start_clients()
try:
    results = analyzer.analyze()
finally:
    analyzer.stop_clients()

# Build suffix index of existing nodes
suffix_index: dict[str, list[str]] = defaultdict(list)
for name in all_node_names:
    # Last segment
    last = name.rsplit(".", 1)[-1]
    suffix_index[last].append(name)
    # Last two segments (module.func)
    parts = name.rsplit(".", 2)
    if len(parts) >= 2:
        suffix_index[parts[-2] + "." + parts[-1]].append(name)

# Analyze what could be recovered
dst_only_missing = [(s, d) for s, d, sm, dm in dropped_edges if not sm and dm]

# Filter to likely in-project (no .venv, no builtins, no pyi)
in_project_dst_missing = [
    (s, d)
    for s, d in dst_only_missing
    if ".venv." not in d and "builtins.pyi" not in d and ".pyi." not in d and "typeshed" not in d
]

print(f"Total dropped edges: {len(dropped_edges)}")
print(f"Dst-only missing: {len(dst_only_missing)}")
print(f"In-project dst-only missing: {len(in_project_dst_missing)}")

# Try suffix matching for in-project missing
recovered = 0
unrecovered = 0
recovery_examples: list[tuple[str, str, str]] = []

for src, dst in in_project_dst_missing:
    last_segment = dst.rsplit(".", 1)[-1]
    candidates = suffix_index.get(last_segment, [])
    if len(candidates) == 1:
        recovered += 1
        if len(recovery_examples) < 20:
            recovery_examples.append((src, dst, candidates[0]))
    elif len(candidates) > 1:
        # Ambiguous - check if any share the same module path
        dst_module = ".".join(dst.split(".")[:-1])
        exact_module = [c for c in candidates if c.startswith(dst_module)]
        if len(exact_module) == 1:
            recovered += 1
            if len(recovery_examples) < 20:
                recovery_examples.append((src, dst, exact_module[0]))
        else:
            unrecovered += 1
    else:
        unrecovered += 1

print(f"\nSuffix-based recovery potential:")
print(f"  Recoverable: {recovered}")
print(f"  Unrecoverable: {unrecovered}")

print(f"\nRecovery examples:")
for src, dst, match in recovery_examples:
    print(f"  {src} -> {dst}")
    print(f"    Matched: {match}")

# Also check src-only missing
src_only_missing = [(s, d) for s, d, sm, dm in dropped_edges if sm and not dm]
in_project_src_missing = [
    (s, d) for s, d in src_only_missing if ".venv." not in s and "builtins.pyi" not in s and ".pyi." not in s
]

print(f"\nSrc-only missing (in-project): {len(in_project_src_missing)}")

src_recovered = 0
src_recovery_examples: list[tuple[str, str, str]] = []
for src, dst in in_project_src_missing:
    last_segment = src.rsplit(".", 1)[-1]
    candidates = suffix_index.get(last_segment, [])
    if len(candidates) == 1:
        src_recovered += 1
        if len(src_recovery_examples) < 20:
            src_recovery_examples.append((src, dst, candidates[0]))
    elif len(candidates) > 1:
        src_module = ".".join(src.split(".")[:-1])
        exact_module = [c for c in candidates if c.startswith(src_module)]
        if len(exact_module) == 1:
            src_recovered += 1
            if len(src_recovery_examples) < 20:
                src_recovery_examples.append((src, dst, exact_module[0]))

print(f"  Recoverable via suffix: {src_recovered}")
print(f"\nSrc recovery examples:")
for src, dst, match in src_recovery_examples:
    print(f"  {src} -> {dst}")
    print(f"    Matched src: {match}")

# Count cross-module recovered edges
cross_module_recovered = 0
for src, dst, match in recovery_examples:
    src_pkg = src.split(".")[0]
    match_pkg = match.split(".")[0]
    if src_pkg != match_pkg:
        cross_module_recovered += 1
        print(f"\n  CROSS-MODULE: {src} -> {match}")

print(f"\nCross-module recovered: {cross_module_recovered}")
