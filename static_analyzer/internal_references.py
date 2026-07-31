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


def reference_tokens(qualified_name: str) -> list[str]:
    return [token.lower() for token in re.split(r"[.:/\\]+", qualified_name) if token]


def parent_qualified_name(qualified_name: str) -> str:
    """Return the class-like parent portion of a qualified symbol name."""
    parent, separator, _ = qualified_name.rpartition(".")
    if not separator:
        return ""
    return parent.split("(", 1)[0]


_TOKEN_CACHE: dict[int, tuple[ReferenceType, set[str], set[str]]] = {}


def _internal_reference_tokens(static_analysis: InternalReferenceSource) -> tuple[set[str], set[str]]:
    """The anchor tokens and flat token set for one static-analysis result.

    Why cached: deriving these walks every reference node in every language and tokenises each
    name, while callers ask per candidate symbol — recomputing turns a per-symbol question into
    a repo-sized scan. Keyed by identity because the results object is large and unhashable; the
    stored weak reference is what makes that safe, since an id is only reused once the original
    is collected and a line whose referent is gone no longer matches.
    """
    key = id(static_analysis)
    cached = _TOKEN_CACHE.get(key)
    if cached is not None and cached[0]() is static_analysis:
        return cached[1], cached[2]
    token_paths = _internal_reference_token_paths(static_analysis)
    anchors = _internal_reference_anchor_tokens(token_paths)
    tokens = {token for path in token_paths for token in path}
    try:
        _TOKEN_CACHE[key] = (ref(static_analysis), anchors, tokens)
    except TypeError:
        pass  # Not weak-referenceable, so it cannot be cached safely; recompute next time.
    return anchors, tokens


def looks_internal_reference(static_analysis: InternalReferenceSource, qualified_name: str) -> bool:
    tokens = reference_tokens(qualified_name)
    if not tokens:
        return False
    anchor_tokens, internal_tokens = _internal_reference_tokens(static_analysis)
    if tokens[0] in anchor_tokens:
        return True
    return any(token.startswith("_") and token in internal_tokens for token in tokens)


def _internal_reference_token_paths(static_analysis: InternalReferenceSource) -> list[list[str]]:
    token_paths: list[list[str]] = []
    for lang in static_analysis.get_languages():
        for node in static_analysis.iter_reference_nodes(lang):
            token_paths.append(reference_tokens(node.fully_qualified_name))
    return token_paths


def _internal_reference_anchor_tokens(token_paths: list[list[str]]) -> set[str]:
    """Return tokens that identify repo-local references without hardcoded layout names."""
    anchors: set[str] = set()
    by_first_token: dict[str, list[list[str]]] = defaultdict(list)

    for tokens in token_paths:
        if not tokens:
            continue
        anchors.add(tokens[0])
        by_first_token[tokens[0]].append(tokens)

        seen: set[str] = set()
        for token in tokens:
            if token in seen:
                anchors.add(token)
            seen.add(token)

    for paths in by_first_token.values():
        second_tokens = {tokens[1] for tokens in paths if len(tokens) > 1}
        if len(second_tokens) > 1:
            anchors.update(second_tokens)

    return anchors
