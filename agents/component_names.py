"""What counts as a component name, and how text is brought to one."""

import re
from collections.abc import Iterable

_IDENTIFIER = re.compile(r"[_/\[\]{}()<>]|[a-z0-9][A-Z]|\w[-.]\w")
"""A token shaped as code: snake_case, kebab-case, camelCase, a dotted or bracketed path."""
_PLACEHOLDER = re.compile(r"Component \d+(\.\d+)*")
_WORD = re.compile(r"[A-Z]+(?![a-z])|[A-Z][a-z0-9]*|[a-z0-9]+")
_MINOR_WORDS = frozenset("a an and as at by for in of on or the to with".split())


def placeholder_name(group_id: str) -> str:
    """The name a component holds until the model names it."""
    return f"Component {group_id}"


def unique_name(base: str, group_id: str, taken: set[str]) -> str:
    """*base*, suffixed with the group id until it is free, and reserved in *taken*."""
    name = base
    suffix = 1
    while name in taken:
        suffix += 1
        name = f"{base} {group_id}" if suffix == 2 else f"{base} {group_id} {suffix - 1}"
    taken.add(name)
    return name


def identifier_path_names(paths: Iterable[str]) -> frozenset[str]:
    """The identifier-shaped directory and file names on *paths*, squashed as :func:`is_natural_name` compares.

    Only identifiers: ``Owner.java`` names a domain word a component may share, while
    ``billing_jobs/`` re-cased as ``Billing Jobs`` still says only where the files are.
    """
    return frozenset(
        _squash(stem)
        for path in paths
        for parts in [path.split("/")]
        for index, segment in enumerate(parts)
        for stem in [segment.rsplit(".", 1)[0] if index == len(parts) - 1 else segment]
        if stem and _IDENTIFIER.search(stem)
    )


def is_natural_name(name: str, path_names: frozenset[str] = frozenset()) -> bool:
    """Whether *name* is natural text: not an identifier, a placeholder, or one of *path_names* re-cased.

    Why: ``billing_jobs``, ``order-store`` and ``Billing Jobs`` (the directory, re-cased) tell the
    reader nothing the file tree does not.
    """
    words = name.split()
    if not words or any("_" in word for word in words) or _PLACEHOLDER.fullmatch(name):
        return False
    if len(words) == 1 and (_IDENTIFIER.search(words[0]) or words[0].islower()):
        return False
    return _squash(name) not in path_names


def humanize(name: str) -> str:
    """*name* as words: ``billing_jobs`` -> ``Billing Jobs``, ``Ordering.API`` -> ``Ordering API``.

    Natural text and placeholders pass through unchanged.
    """
    if is_natural_name(name) or _PLACEHOLDER.fullmatch(name):
        return name
    parts = name.split()
    words = [word for part in parts for word in (_WORD.findall(part) if len(parts) == 1 or "_" in part else [part])]
    return " ".join(
        word if index and word in _MINOR_WORDS else word[:1].upper() + word[1:] for index, word in enumerate(words)
    )


def _squash(text: str) -> str:
    return re.sub(r"[^0-9a-z]", "", text.casefold())
