"""Words: how a qualified name is split, stemmed and classified."""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterable

_TOKEN = re.compile(r"[A-Z]+(?![a-z])|[A-Z][a-z0-9]*|[a-z0-9]+")
_GENERIC_ARITY = re.compile(r"`\d+$")
_INTERFACE_PREFIX = re.compile(r"^I(?=[A-Z][a-z])")


def tokenize(identifier: str) -> tuple[str, ...]:
    """Split an identifier into words: CamelCase, snake_case, runs of capitals (``HTTPServer``).

    Drops a parameter list, generic arguments, a C# generic arity suffix, the ``I`` a C#/Java
    interface name starts with, and bare numbers.
    """
    name = identifier.split("(", 1)[0]
    name = _GENERIC_ARITY.sub("", name)
    name = re.sub(r"<[^<>]*>", "", name)
    name = _INTERFACE_PREFIX.sub("", name.strip())
    return tuple(word for word in _TOKEN.findall(name) if not word.isdigit())


def stem(word: str) -> str:
    """Fold the inflections identifiers carry, so ``Ordering``, ``Orders`` and ``Order`` are one word.

    Idempotent: ``Mappings`` and ``Mapping`` reach the same stem.
    """
    lowered = word.casefold()
    while True:
        stripped = _strip_one_suffix(lowered)
        if stripped == lowered:
            return lowered
        lowered = stripped


def _strip_one_suffix(lowered: str) -> str:
    for suffix, replacement in (("ies", "y"), ("ing", ""), ("ions", "ion"), ("ed", ""), ("es", ""), ("s", "")):
        if len(lowered) <= len(suffix) + 2 or not lowered.endswith(suffix):
            continue
        if suffix == "es" and lowered[-3] not in "sxzh":
            # ``services``, ``types``, ``nodes``: the ``e`` is part of the word.
            return lowered[:-1]
        if suffix == "s" and lowered[-2] in "sui":
            # ``class``, ``status``, ``analysis`` are singular.
            return lowered
        return lowered[: -len(suffix)] + replacement
    return lowered


def stems(identifier: str) -> tuple[str, ...]:
    return tuple(stem(word) for word in tokenize(identifier))


def segments(qualified_name: str, delimiter: str) -> list[str]:
    """Split on *delimiter*, keeping parameter lists and generic arguments whole."""
    out: list[str] = []
    depth = 0
    current: list[str] = []
    for char in qualified_name:
        if char in "(<":
            depth += 1
        elif char in ")>":
            depth -= 1
        if char == delimiter and depth == 0:
            out.append("".join(current))
            current = []
        else:
            current.append(char)
    out.append("".join(current))
    return [part for part in out if part]


LAYOUT_WORDS = frozenset(
    {"src", "main", "java", "kotlin", "scala", "lib", "libs", "packages", "pkg", "apps", "modules"}
    | {"source", "sources", "python", "go", "js", "ts"}
)
"""Segments naming a build layout rather than a scope. The walk steps through them."""

ROLE_WORDS = frozenset(
    stem(word)
    for word in """
    api apis application applications domain infrastructure infra contracts contract platform core
    common shared util utils utilities helpers helper extensions extension abstractions abstraction
    interfaces interface impl implementation internal internals models model viewmodels viewmodel views
    view services service controllers controller repositories repository persistence data dto dtos
    commands command queries query handlers handler events event behaviors behaviours validations
    validation validators validator exceptions exception config configuration configurations settings
    options middleware middlewares filters filter mappers mapper mapping converters converter
    resources resource assets components component pages page layouts layout hooks hook types typings
    constants enums entities entity migrations migration seed seeds fixtures fixture mocks mock tests
    test testing spec specs e2e docs doc examples example samples sample scripts tools tooling bin
    build dist out generated gen proto protos client clients server servers web ui app apps lib
    base framework frameworks integration integrations io net http grpc rest graphql
    init index main mod module program startup
    """.split()
)
"""The closed class of words that name how software is built rather than what it is about.

Why fixed: it cannot be learned from identifier frequencies; a planner may add a per-repo tail.
"""


MIN_UBIQUITOUS = 3


def ubiquitous_words(names: Iterable[str]) -> frozenset[str]:
    """Words carried by at least three siblings and by at least half of them: a product namesake.

    Why frequency, not intersection: one odd sibling must not keep the product name alive as
    everyone's distinctive word. Why three: two of a few siblings sharing a word is kinship.
    """
    listed = list(names)
    counts = Counter(word for name in listed for word in set(stems(name)))
    return frozenset(word for word, count in counts.items() if count >= MIN_UBIQUITOUS and count >= len(listed) / 2)


def is_role_named(name: str, role_words: frozenset[str], ubiquitous: frozenset[str] = frozenset()) -> bool:
    """Whether every word of *name* (the ubiquitous ones aside) is a role word."""
    words = [word for word in stems(name) if word not in ubiquitous]
    return bool(words) and all(word in role_words for word in words)


def distinctive_word(name: str, role_words: frozenset[str], ubiquitous: frozenset[str] = frozenset()) -> str:
    """The first word of *name* that names this thing rather than every thing, else ``""``."""
    for word in stems(name):
        if word not in role_words and word not in ubiquitous:
            return word
    return ""
