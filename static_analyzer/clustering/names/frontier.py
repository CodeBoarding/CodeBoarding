"""The frontier: where the walk down the trie stops, and what it emits there.

Per node: step through a single child, a layout word, or a child holding nearly everything;
a node whose children are mostly role-named is layered, and is transposed when feature names
recur under at least two of its layers, else it is one box; a feature-named child is opened
only while it holds a share of the scope; a role-named child is a box, never a way in; every
other child is a box, and a box of one unit is a loose unit of its parent. The walk emits
rules, never assignments: ``replay`` decides which unit lands where, on this run and every
later one.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from static_analyzer.clustering.names.inventory import Trie, TrieNode
from static_analyzer.clustering.names.spec import Prefix
from static_analyzer.clustering.names.tokens import (
    LAYOUT_WORDS,
    distinctive_word,
    is_role_named,
    stems,
    ubiquitous_words,
)

SHARE = 0.5
"""A feature-named child is opened only while it holds at least this share of the scope's
units: half the scope is the scope's structure, less is one of its parts. Measured identical
to 0.25 on seven rulers and better on the eighth; 0.25 opens both of a repository's two big
packages and scatters their contents across the root."""

NEARLY_ALL = 0.8
"""A child holding this share of its parent is stepped through whatever it is called."""

ROLE_SHARE = 0.6
"""A node this much of whose units sit under role-named children is a layered node."""

MIN_LAYERS = 3

BOX = "box"
FEATURE = "feature"
LOOSE = "loose"
RESIDUAL = "residual"
WORD = "word"


@dataclass(frozen=True)
class Candidate:
    """One thing the frontier proposes as a box, before any grouping."""

    key: str
    kind: str
    label: str
    prefixes: tuple[Prefix, ...] = ()
    fallback_prefixes: tuple[Prefix, ...] = ()
    terms: tuple[str, ...] = ()


@dataclass
class Frontier:
    candidates: list[Candidate] = field(default_factory=list)
    axis: str = "flat"
    notes: list[str] = field(default_factory=list)


def walk(trie: Trie, role_words: frozenset[str], *, share: float = SHARE, transpose: bool = True) -> Frontier:
    """The candidates of a trie.

    A layered node whose features recur is transposed onto them only with ``transpose``, else
    kept as one box; one whose features do not recur is drawn layer by layer. Why the switch:
    below the leaf cap a transposition leaves a residual per layer, which reads as a grab bag;
    layers are directories a reader can name.
    """
    frontier = Frontier()
    top = trie.root
    while len(top.children) == 1 and not top.units:
        top = next(iter(top.children.values()))
    _visit(top, top.count, role_words, share, frontier, transpose, top=True)
    return frontier


def _visit(
    node: TrieNode,
    total: int,
    role_words: frozenset[str],
    share: float,
    out: Frontier,
    transpose: bool,
    *,
    top: bool = False,
) -> None:
    children = sorted(node.children.items())
    if len(children) == 1 and not node.units:
        _visit(children[0][1], total, role_words, share, out, transpose, top=top)
        return
    ubiquitous = ubiquitous_words(name for name, _ in children)
    stepped = {name for name, child in children if _stepped_through(name, child, node)}
    role_units = sum(child.count for name, child in children if is_role_named(name, role_words, ubiquitous))
    if not stepped and len(children) >= MIN_LAYERS and node.count and role_units / node.count >= ROLE_SHARE:
        _layered(node, ubiquitous, role_words, out, transpose, top=top)
        return
    if top:
        out.axis = "structural"
    loose = bool(node.units)
    scopes = 0
    for name, child in children:
        if child.count == 1:
            loose = True
            continue
        scopes += 1
        if name in stepped:
            _visit(child, total, role_words, share, out, transpose)
            continue
        if is_role_named(name, role_words, ubiquitous):
            out.candidates.append(_box(child))
            continue
        child_names = sorted(child.children)
        child_ubiquitous = ubiquitous_words(child_names)
        child_role_units = sum(
            grandchild.count
            for grandchild_name, grandchild in child.children.items()
            if is_role_named(grandchild_name, role_words, child_ubiquitous)
        )
        child_role_share = child_role_units / child.count if child.count else 1.0
        dominant = total > 0 and child.count / total >= share
        if dominant and child_names and child_role_share < ROLE_SHARE:
            out.notes.append(f"opened {_dotted(child.path)} ({child.count} units)")
            _visit(child, total, role_words, share, out, transpose)
        elif dominant and len(child_names) >= MIN_LAYERS and child_role_share >= ROLE_SHARE:
            _layered(child, child_ubiquitous, role_words, out, transpose)
        else:
            out.candidates.append(_box(child))
    if not scopes:
        # Only units and one-unit children: a flat scope is one box, not a pile of loose files.
        out.candidates.append(_box(node))
    elif loose:
        out.candidates.append(_loose(node))


def _stepped_through(name: str, child: TrieNode, parent: TrieNode) -> bool:
    return name.casefold() in LAYOUT_WORDS or child.count >= NEARLY_ALL * parent.count


def _layered(
    node: TrieNode,
    ubiquitous: frozenset[str],
    role_words: frozenset[str],
    out: Frontier,
    transpose: bool,
    *,
    top: bool = False,
) -> None:
    """A node whose children are layers: transpose onto the features recurring under two of
    them, else draw the layers, the only structure there is."""
    layers = sorted(node.children.items())
    ubiquitous = ubiquitous | ubiquitous_words(name for _, layer in layers for name in layer.children)
    layers_by_feature: dict[str, set[str]] = {}
    display: dict[str, str] = {}
    for layer_name, layer in layers:
        for name, _ in _feature_directories(layer, role_words, ubiquitous):
            feature = _feature_stem(name, role_words, ubiquitous)
            layers_by_feature.setdefault(feature, set()).add(layer_name)
            display.setdefault(feature, name)
    features = sorted(feature for feature, feature_layers in layers_by_feature.items() if len(feature_layers) >= 2)
    if len(features) >= 2 and not transpose:
        out.notes.append(f"{_dotted(node.path) or '<root>'}: layered, kept as one box")
        out.candidates.append(_box(node))
        return
    if len(features) < 2:
        if top:
            out.axis = "structural"
        out.notes.append(
            f"{_dotted(node.path) or '<root>'}: role-named children, no recurring features; layers as boxes"
        )
        for _, layer in sorted(node.children.items()):
            if layer.count > 1:
                out.candidates.append(_box(layer))
        if node.units or any(layer.count == 1 for layer in node.children.values()):
            out.candidates.append(_loose(node))
        return
    if top:
        out.axis = "transposed"
    out.notes.append(f"transposed {_dotted(node.path) or '<root>'} onto {', '.join(features)}")
    prefixes_by_feature: dict[str, list[Prefix]] = {feature: [] for feature in features}
    for _, layer in layers:
        _collect_feature_prefixes(layer, set(features), role_words, ubiquitous, prefixes_by_feature)
    for feature in features:
        out.candidates.append(
            Candidate(
                key=f"{FEATURE}:{_dotted(node.path)}:{feature}",
                kind=FEATURE,
                label=display[feature],
                prefixes=tuple(prefixes_by_feature[feature]),
                terms=(feature,),
            )
        )
    if node.units:
        out.candidates.append(_loose(node))
    for name, layer in layers:
        out.candidates.append(
            Candidate(
                key=f"{RESIDUAL}:{_dotted(layer.path)}",
                kind=RESIDUAL,
                label=name,
                fallback_prefixes=(layer.path,),
            )
        )


def _feature_directories(
    node: TrieNode, role_words: frozenset[str], ubiquitous: frozenset[str]
) -> list[tuple[str, TrieNode]]:
    """The shallowest feature-named directories under a layer, looking through its role-named ones."""
    found: list[tuple[str, TrieNode]] = []
    for name, child in sorted(node.children.items()):
        if is_role_named(name, role_words, ubiquitous):
            found.extend(_feature_directories(child, role_words, ubiquitous))
        else:
            found.append((name, child))
    return found


def _collect_feature_prefixes(
    node: TrieNode,
    features: set[str],
    role_words: frozenset[str],
    ubiquitous: frozenset[str],
    prefixes_by_feature: dict[str, list[Prefix]],
) -> None:
    """The shallowest node under a layer whose first word is a feature keys its subtree."""
    for name, child in sorted(node.children.items()):
        feature = _feature_stem(name, role_words, ubiquitous)
        if not is_role_named(name, role_words, ubiquitous) and feature in features:
            prefixes_by_feature[feature].append(child.path)
            continue
        _collect_feature_prefixes(child, features, role_words, ubiquitous, prefixes_by_feature)


def _box(node: TrieNode) -> Candidate:
    return Candidate(
        key=f"{BOX}:{_dotted(node.path)}", kind=BOX, label=node.path[-1] if node.path else "", prefixes=(node.path,)
    )


def _loose(node: TrieNode) -> Candidate:
    return Candidate(key=f"{LOOSE}:{_dotted(node.path)}", kind=LOOSE, label="", fallback_prefixes=(node.path,))


def _feature_stem(name: str, role_words: frozenset[str], ubiquitous: frozenset[str]) -> str:
    """The word a directory is about: its first word that is neither a role nor the product's name."""
    words = stems(name)
    return distinctive_word(name, role_words, ubiquitous) or (words[0] if words else name.casefold())


def _dotted(path: Prefix) -> str:
    return ".".join(path)
