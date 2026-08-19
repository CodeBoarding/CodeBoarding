"""Render a CallGraph into the text an LLM sees."""

from __future__ import annotations

import logging
from collections import defaultdict
from collections.abc import Sequence

from static_analyzer.cfg.call_graph import CallGraph
from static_analyzer.config import NodeType
from static_analyzer.node import Node

logger = logging.getLogger(__name__)

DEFAULT_SIZE_LIMIT = 2_500_000


def render_call_graph(graph: CallGraph, size_limit: int = DEFAULT_SIZE_LIMIT, skip_nodes: Sequence[Node] = ()) -> str:
    """Render at method-level detail, dropping to a class-level summary past ``size_limit``."""
    skip_set = set(skip_nodes)

    detailed = _render_detailed(graph, skip_set)
    logger.info(f"[CFG Tool] LLM string: {len(detailed)} characters, size limit: {size_limit} characters")
    if len(detailed) <= size_limit:
        return detailed

    logger.info(
        f"[CallGraph] Control flow graph is too large ({len(detailed)} chars), switching to class-level summary."
    )
    class_level = _render_class_level(graph, skip_set)
    logger.info(f"[CallGraph] Class-level summary: {len(class_level)} characters")
    return class_level


def _visible_targets(graph: CallGraph, node: Node, skip_set: set[Node]) -> list[str]:
    """Sorted call targets of ``node``, minus those resolving to a skipped node."""
    visible = []
    for name in node.methods_called_by_me:
        target = graph.nodes.get(name)
        if target is not None and target in skip_set:
            continue
        visible.append(name)
    return sorted(visible)


def _render_detailed(graph: CallGraph, skip_set: set[Node]) -> str:
    """File-grouped, method-level detail with call targets."""
    file_nodes: dict[str, list[Node]] = defaultdict(list)
    for node in graph.nodes.values():
        if node not in skip_set:
            file_nodes[node.file_path].append(node)

    active_nodes = sum(len(v) for v in file_nodes.values())
    active_edges = sum(
        1
        for e in graph.edges
        if graph.nodes[e.get_source()] not in skip_set and graph.nodes[e.get_destination()] not in skip_set
    )

    result = f"Control flow graph with {active_nodes} nodes and {active_edges} edges\n"

    for file_path in sorted(file_nodes):
        nodes = sorted(file_nodes[file_path], key=lambda n: n.fully_qualified_name)
        for node in nodes:
            targets = _visible_targets(graph, node, skip_set)
            if targets:
                label = node.entity_label()
                result += f"{label} {node.fully_qualified_name} calls: {', '.join(targets)}\n"

    return result


def _render_class_level(graph: CallGraph, skip_set: set[Node]) -> str:
    """Class-to-class summary with call counts and up to three example method pairs."""
    delimiter = graph.delimiter
    class_calls: dict[str, dict[str, list[str]]] = defaultdict(lambda: defaultdict(list))
    function_calls: list[str] = []

    for node in graph.nodes.values():
        if node in skip_set:
            continue
        targets = _visible_targets(graph, node, skip_set)
        if not targets:
            continue

        parts = node.fully_qualified_name.split(delimiter)
        if node.type == NodeType.METHOD and len(parts) > 1:
            class_name = delimiter.join(parts[:-1])
            method_short = parts[-1]

            for called_method in targets:
                called_parts = called_method.split(delimiter)
                if len(called_parts) > 1:
                    called_class = delimiter.join(called_parts[:-1])
                    called_short = called_parts[-1]
                    class_calls[class_name][called_class].append(f"{method_short}->{called_short}")
                else:
                    class_calls[class_name][called_method].append(f"{method_short}->{called_method}")
        else:
            function_calls.append(f"Function {node.fully_qualified_name} calls: {', '.join(targets)}")

    active_count = sum(1 for n in graph.nodes.values() if n not in skip_set)
    result = f"Control flow graph with {active_count} nodes (class-level summary)\n"

    for class_name in sorted(class_calls):
        called_targets = class_calls[class_name]
        target_strs = []
        for target_class in sorted(called_targets):
            edges = called_targets[target_class]
            count = len(edges)
            examples = ", ".join(edges[:3])
            suffix = f" +{count - 3} more" if count > 3 else ""
            target_strs.append(f"{target_class} ({count} calls: {examples}{suffix})")
        result += f"Class {class_name} -> {'; '.join(target_strs)}\n"

    for func_call in function_calls:
        result += func_call + "\n"

    return result
