"""Canonical projection of static-analysis ``Node``s to ``FileMethodGroup``s.

Single source of truth for "what methods exist in a file" used by both
the full-analysis path (``ClusterMethodsMixin._build_file_methods_from_nodes``)
and the incremental resolver. Keeping these aligned is what lets the
incremental fast paths see the same method identities the persisted
baseline uses; otherwise the same physical symbol shows up twice (alias
qualified names) or vanishes from one side (anonymous-callback drop)
and the diff fabricates added/deleted entries.

Why these rules and not others:
  - Type filter (``CALLABLE_TYPES | CLASS_TYPES``) excludes variables /
    constants / properties, matching the persisted format.
  - Dedupe key ``(start_line, end_line, node_type.name, short_name)``
    collapses alias qualified names (``a.b.foo`` and ``x.b.foo``) that
    point at the same physical symbol.
  - On a tie, the more specific qualified name wins (longer dotted path
    when the leaf segments match; otherwise longer string overall).
  - No ``is_callback_or_anonymous()`` filter — anonymous callbacks ARE
    persisted in ``methods_index``, so dropping them here would re-create
    the bug this module exists to fix.
"""

import os
from collections import defaultdict
from collections.abc import Iterable
from pathlib import Path

from agents.agent_responses import FileMethodGroup, MethodEntry
from static_analyzer.constants import CALLABLE_TYPES, CLASS_TYPES
from static_analyzer.node import Node


def _is_more_specific(candidate: str, current: str) -> bool:
    """Pick the more specific qname when two aliases collide on the dedupe key.

    Example: ``module.Class.method`` beats ``module.method`` for the same
    symbol span. When leaf segments differ, longer-wins is the simplest
    stable tiebreaker.
    """
    candidate_parts = candidate.split(".")
    current_parts = current.split(".")
    if candidate_parts[-1] == current_parts[-1]:
        return len(candidate_parts) > len(current_parts)
    return len(candidate) > len(current)


def build_file_method_groups(nodes: Iterable[Node], repo_dir: Path) -> list[FileMethodGroup]:
    """Group callable / class ``Node``s into deduped per-file ``FileMethodGroup``s.

    ``repo_dir`` is used to convert absolute ``node.file_path`` to a repo-
    relative key. Already-relative paths pass through unchanged.
    """
    allowed_types = CALLABLE_TYPES | CLASS_TYPES
    by_file: dict[str, dict[tuple[int, int, str, str], MethodEntry]] = defaultdict(dict)

    for node in nodes:
        if node.type not in allowed_types:
            continue

        rel_path = os.path.relpath(node.file_path, repo_dir) if os.path.isabs(node.file_path) else node.file_path

        method_name = node.fully_qualified_name.split(".")[-1]
        dedupe_key = (node.line_start, node.line_end, node.type.name, method_name)
        candidate = MethodEntry(
            qualified_name=node.fully_qualified_name,
            start_line=node.line_start,
            end_line=node.line_end,
            node_type=node.type.name,
        )

        existing = by_file[rel_path].get(dedupe_key)
        if existing is None or _is_more_specific(candidate.qualified_name, existing.qualified_name):
            by_file[rel_path][dedupe_key] = candidate

    groups: list[FileMethodGroup] = []
    for file_path in sorted(by_file):
        methods = sorted(by_file[file_path].values(), key=lambda m: (m.start_line, m.end_line, m.qualified_name))
        groups.append(FileMethodGroup(file_path=file_path, methods=methods))
    return groups
