"""Units and the trie of their positions.

A unit is one file: the qualified names the engine declared in it. Its *position* is the
file's directory under the repository root, so the trie over every unit's position is the
repository's directory tree, the same for every language and every engine, whatever an
adapter chose to call a symbol. Its *key* is the position plus the file's stem: what a rule
owns to claim this file and not a sibling in the same directory.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path

from repo_utils.path_utils import normalize_repo_path
from static_analyzer.cfg import CallGraph

PROJECT_MANIFESTS = (
    "package.json",
    "pyproject.toml",
    "setup.py",
    "go.mod",
    "Cargo.toml",
    "pom.xml",
    "build.gradle",
    "build.gradle.kts",
    "composer.json",
)
"""Files that make a directory a project of its own; a ``.csproj`` or ``.fsproj`` does too."""


@dataclass(frozen=True)
class Unit:
    """One file's declarations. ``unit_id`` is the engine's path, used only as an identity."""

    unit_id: str
    language: str
    names: tuple[str, ...]
    position: tuple[str, ...]
    key: tuple[str, ...]
    project: bool = False
    """Whether the file's own directory is a project root: a unit a reader separated on purpose."""


def units_from_graph(graph: CallGraph, language: str, repo_dir: Path | None = None) -> list[Unit]:
    """One unit per file, positioned by its directory under ``repo_dir``.

    ``repo_dir`` is required when the graph carries absolute paths; a relative path is taken
    as already repository-relative.
    """
    by_file: dict[str, list[str]] = {}
    for qualified_name, node in graph.nodes.items():
        if node.file_path:
            by_file.setdefault(node.file_path, []).append(qualified_name)
    units: list[Unit] = []
    projects: dict[tuple[str, ...], bool] = {}
    for file_path, names in sorted(by_file.items()):
        if Path(file_path).is_absolute() and repo_dir is None:
            raise ValueError(f"{file_path} is absolute and no repository root was given to position it")
        relative = Path(normalize_repo_path(file_path, repo_dir))
        position = relative.parent.parts
        if position == (".",):
            position = ()
        if position not in projects:
            projects[position] = repo_dir is not None and _is_project_root(repo_dir / Path(*position))
        units.append(
            Unit(
                file_path,
                language,
                tuple(sorted(names)),
                position,
                position + (relative.stem,),
                projects[position],
            )
        )
    return units


def units_from_graphs(graphs: Mapping[str, CallGraph], repo_dir: Path | None = None) -> list[Unit]:
    units: list[Unit] = []
    for language in sorted(graphs):
        units.extend(units_from_graph(graphs[language], language, repo_dir))
    return units


def _is_project_root(directory: Path) -> bool:
    if any((directory / manifest).is_file() for manifest in PROJECT_MANIFESTS):
        return True
    try:
        return any(entry.suffix in (".csproj", ".fsproj") for entry in directory.iterdir())
    except OSError:
        return False


@dataclass
class TrieNode:
    path: tuple[str, ...]
    children: dict[str, TrieNode] = field(default_factory=dict)
    units: list[Unit] = field(default_factory=list)
    count: int = 0
    """Units in this subtree, set once the trie is built."""


class Trie:
    """The prefix tree of unit positions: the directory tree of every file the engine saw.

    A node holding one unit and nothing else is a one-file directory; the walk treats such a
    node as a loose unit of its parent rather than as a scope, but keeps it in the tree,
    because a one-file feature directory under a layer is evidence the transposition needs.
    """

    def __init__(self, units: Iterable[Unit]):
        self.root = TrieNode(())
        for unit in units:
            node = self.root
            for depth, segment in enumerate(unit.position):
                node = node.children.setdefault(segment, TrieNode(unit.position[: depth + 1]))
            node.units.append(unit)
        _count(self.root)

    def node(self, path: tuple[str, ...]) -> TrieNode | None:
        node = self.root
        for segment in path:
            child = node.children.get(segment)
            if child is None:
                return None
            node = child
        return node


def _count(node: TrieNode) -> int:
    node.count = len(node.units) + sum(_count(child) for child in node.children.values())
    return node.count
