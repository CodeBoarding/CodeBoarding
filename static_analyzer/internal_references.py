import re
from collections import defaultdict
from collections.abc import Iterable
from typing import Protocol

from static_analyzer.constants import Language


class ReferenceNode(Protocol):
    @property
    def id(self) -> str: ...


class InternalReferenceSource(Protocol):
    def get_languages(self) -> list[Language]: ...

    def iter_reference_nodes(self, language: Language | None = None) -> Iterable[ReferenceNode]: ...


def reference_tokens(qualified_name: str) -> list[str]:
    return [token.lower() for token in re.split(r"[.:/\\]+", qualified_name) if token]


def parent_qualified_name(qualified_name: str) -> str:
    """Return the class-like parent portion of a qualified symbol name."""
    parent, separator, _ = qualified_name.rpartition(".")
    if not separator:
        return ""
    return parent.split("(", 1)[0]


def looks_internal_reference(static_analysis: InternalReferenceSource, qualified_name: str) -> bool:
    tokens = reference_tokens(qualified_name)
    if not tokens:
        return False
    internal_token_paths = _internal_reference_token_paths(static_analysis)
    if tokens[0] in _internal_reference_anchor_tokens(internal_token_paths):
        return True
    internal_tokens = {token for path in internal_token_paths for token in path}
    return any(token.startswith("_") and token in internal_tokens for token in tokens)


def _internal_reference_token_paths(static_analysis: InternalReferenceSource) -> list[list[str]]:
    token_paths: list[list[str]] = []
    for lang in static_analysis.get_languages():
        for node in static_analysis.iter_reference_nodes(lang):
            token_paths.append(reference_tokens(node.id))
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
