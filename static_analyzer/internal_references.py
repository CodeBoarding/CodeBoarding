import re
from collections import defaultdict
from collections.abc import Iterable
from typing import Protocol
from weakref import ReferenceType, ref

from static_analyzer.constants import Language


class ReferenceNode(Protocol):
    fully_qualified_name: str


class InternalReferenceSource(Protocol):
    def get_languages(self) -> list[Language]: ...

    def iter_reference_nodes(self, lang: Language) -> Iterable[ReferenceNode]: ...


def qualified_symbol_parts(qualified_name: str) -> list[str]:
    return [part.lower() for part in re.split(r"[.:/\\]+", qualified_name) if part]


def parent_qualified_name(qualified_name: str) -> str:
    """Return the class-like parent portion of a qualified symbol name."""
    parent, separator, _ = qualified_name.rpartition(".")
    if not separator:
        return ""
    return parent.split("(", 1)[0]


_SYMBOL_PARTS_CACHE: dict[int, tuple[ReferenceType, set[str], set[str]]] = {}


def _internal_reference_symbol_parts(static_analysis: InternalReferenceSource) -> tuple[set[str], set[str]]:
    """The anchor parts and flat symbol-part set for one static-analysis result.

    Why cached: deriving these walks every reference node in every language and tokenises each
    name, while callers ask per candidate symbol — recomputing turns a per-symbol question into
    a repo-sized scan. Keyed by identity because the results object is large and unhashable; the
    stored weak reference is what makes that safe, since an id is only reused once the original
    is collected and a line whose referent is gone no longer matches.
    """
    key = id(static_analysis)
    cached = _SYMBOL_PARTS_CACHE.get(key)
    if cached is not None and cached[0]() is static_analysis:
        return cached[1], cached[2]
    symbol_part_paths = _internal_reference_symbol_part_paths(static_analysis)
    anchors = _internal_reference_anchor_parts(symbol_part_paths)
    symbol_parts = {part for path in symbol_part_paths for part in path}
    try:
        _SYMBOL_PARTS_CACHE[key] = (ref(static_analysis), anchors, symbol_parts)
    except TypeError:
        pass  # Not weak-referenceable, so it cannot be cached safely; recompute next time.
    return anchors, symbol_parts


def looks_internal_reference(static_analysis: InternalReferenceSource, qualified_name: str) -> bool:
    symbol_parts = qualified_symbol_parts(qualified_name)
    if not symbol_parts:
        return False
    anchor_parts, internal_parts = _internal_reference_symbol_parts(static_analysis)
    if symbol_parts[0] in anchor_parts:
        return True
    return any(part.startswith("_") and part in internal_parts for part in symbol_parts)


def _internal_reference_symbol_part_paths(static_analysis: InternalReferenceSource) -> list[list[str]]:
    symbol_part_paths: list[list[str]] = []
    for lang in static_analysis.get_languages():
        for node in static_analysis.iter_reference_nodes(lang):
            symbol_part_paths.append(qualified_symbol_parts(node.fully_qualified_name))
    return symbol_part_paths


def _internal_reference_anchor_parts(symbol_part_paths: list[list[str]]) -> set[str]:
    """Return symbol parts that identify repo-local references without hardcoded layout names."""
    anchors: set[str] = set()
    by_first_part: dict[str, list[list[str]]] = defaultdict(list)

    for parts in symbol_part_paths:
        if not parts:
            continue
        anchors.add(parts[0])
        by_first_part[parts[0]].append(parts)

        seen: set[str] = set()
        for part in parts:
            if part in seen:
                anchors.add(part)
            seen.add(part)

    for paths in by_first_part.values():
        second_parts = {parts[1] for parts in paths if len(parts) > 1}
        if len(second_parts) > 1:
            anchors.update(second_parts)

    return anchors
