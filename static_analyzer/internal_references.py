import re
from collections.abc import Iterable
from typing import Protocol

from static_analyzer.constants import Language


class ReferenceNode(Protocol):
    fully_qualified_name: str


class InternalReferenceSource(Protocol):
    def get_languages(self) -> list[Language]: ...

    def iter_reference_nodes(self, lang: Language) -> Iterable[ReferenceNode]: ...


def reference_tokens(qualified_name: str) -> list[str]:
    return [token.lower() for token in re.split(r"[.:/\\]+", qualified_name) if token]


def looks_internal_reference(static_analysis: InternalReferenceSource, qualified_name: str) -> bool:
    tokens = reference_tokens(qualified_name)
    if not tokens:
        return False
    internal_tokens = _internal_reference_tokens(static_analysis)
    roots = {token for token in internal_tokens if token not in {"packages", "src", "lib"}}
    if tokens[0] in roots:
        return True
    return any(token.startswith("_") and token in internal_tokens for token in tokens)


def _internal_reference_tokens(static_analysis: InternalReferenceSource) -> set[str]:
    tokens: set[str] = set()
    for lang in static_analysis.get_languages():
        for node in static_analysis.iter_reference_nodes(lang):
            tokens.update(reference_tokens(node.fully_qualified_name))
    return tokens
