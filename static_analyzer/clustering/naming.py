"""Partition symbols by what their qualified names say.

A qualified name has two halves. ``src/Catalog.API/Model/CatalogItem`` is structural
(``Catalog.API``, ``Model``) and lexical (``Catalog``, ``Item``). Which half carries the
architecture is a property of the repo, not of the algorithm: where the top-level scopes are
features the structural half is the answer, and where they are layers -- ``Api``,
``Application``, ``Domain``, ``Infrastructure`` -- the feature survives only in the
identifiers, and grouping by scope reproduces the layering.

The components, the vocabulary each owns, the machinery vocabulary and the per-scope
feature/layer call arrive as a :class:`NamingModel` decided once per full analysis.
Everything here is arithmetic over names, so an incremental run reusing the model reproduces
the same partition exactly.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import PurePosixPath

from clustering_ids import ClusterId
from static_analyzer.cfg import CallGraph
from static_analyzer.clustering.models import ClusterResult

INFRASTRUCTURE = "Infrastructure"
"""Where symbols carrying no domain vocabulary go. One named component rather than a
scattering: a symbol the vocabulary cannot place is evidence of absence, and spreading it
across the others invents membership the names do not support."""

BUILD_ROOTS = frozenset({"src", "lib", "source", "sources", "packages", "apps", "modules", "pkg"})
"""Directories that name a build layout rather than a scope, so they are no part of a name."""

_TOKEN = re.compile(r"[A-Z]+(?![a-z])|[A-Z][a-z0-9]*|[a-z0-9]+")
_GENERIC_ARITY = re.compile(r"`\d+$")
_INTERFACE_PREFIX = re.compile(r"^I(?=[A-Z])")
_INFLECTIONS = ("ing", "ies", "es", "s")

FILE_STRATEGY = "file_leaves"
"""Recorded on a ``ClusterResult`` whose leaves are files rather than graph communities."""

SCOPES_ARE_COMPONENTS_RATIO = 0.25
"""How many scopes the components must name before the structural half leads. The measured
gap is wide -- 0.00 against 0.50 -- so the exact value is not load-bearing."""


def tokenize(identifier: str) -> tuple[str, ...]:
    """Split an identifier into its words.

    Handles CamelCase, snake_case and runs of capitals (``HTTPServer`` -> ``HTTP``,
    ``Server``). Drops a parameter list, generic arguments, a C# generic arity suffix, and
    the ``I`` a C#/Java interface name starts with.
    """
    name = identifier.split("(", 1)[0]
    name = _GENERIC_ARITY.sub("", name)
    name = re.sub(r"<[^<>]*>", "", name)
    name = _INTERFACE_PREFIX.sub("", name.strip())
    return tuple(_TOKEN.findall(name))


def stem(word: str) -> str:
    """Fold the inflections a name carries, so ``Ordering`` and ``Order`` are one word."""
    lowered = word.casefold()
    for suffix in _INFLECTIONS:
        if len(lowered) > len(suffix) + 2 and lowered.endswith(suffix):
            return lowered[: -len(suffix)]
    return lowered


def scope_of(file_path: str, repo_root: str = "") -> str:
    """The outermost scope a file is declared under, ignoring build roots.

    *file_path* may be absolute -- the engine's nodes carry absolute paths -- in which case
    *repo_root* is stripped first. Without it every file's outermost part is ``/``, one scope
    covers the repo, and the structural half can never lead.
    """
    path = PurePosixPath(file_path)
    if repo_root:
        root = PurePosixPath(repo_root)
        if path.is_relative_to(root):
            path = path.relative_to(root)
    parts = [part for part in path.parts[:-1] if part.casefold() not in BUILD_ROOTS and part != "/"]
    return parts[0] if parts else ""


def ubiquitous_words(scope_names: set[str]) -> frozenset[str]:
    """Words every scope carries, which therefore distinguish no scope from another.

    ``Modulify.Catalog``, ``Modulify.Player`` and ``Modulify.Library`` all start with
    ``Modulify``; keeping it leaves every scope one word and collapses the repo to a single
    component.
    """
    per_scope = [{stem(word) for word in tokenize(name)} for name in scope_names if name]
    return frozenset(set.intersection(*per_scope)) if per_scope else frozenset()


@dataclass(frozen=True)
class ComponentVocabulary:
    """A component the naming model proposes, and the vocabulary it owns."""

    name: str
    owns: tuple[str, ...]


@dataclass(frozen=True)
class NamingModel:
    """The per-repo decision an incremental run reuses verbatim."""

    components: tuple[ComponentVocabulary, ...]
    machinery: frozenset[str]
    """Vocabulary naming how software is built rather than what this system is about. It
    never owns a component, so ``Handler`` cannot gather every handler into one box."""

    def owner_by_word(self) -> dict[str, str]:
        owner: dict[str, str] = {}
        machinery = {word.casefold() for word in self.machinery}
        for component in self.components:
            for word in component.owns:
                key = stem(word)
                if key not in machinery and word.casefold() not in machinery:
                    owner.setdefault(key, component.name)
        return owner

    def scopes_are_components(self, scope_names: set[str]) -> bool:
        """Whether this model's own components name the scopes.

        The structural half leads only when they do. A model proposing Incidents,
        Escalation and Analytics while the scopes are ``Beacon.Application`` and
        ``Beacon.Infrastructure`` is contradicting itself, and grouping by scope there
        reproduces the layering -- which scores below not partitioning at all. Measured
        across rulers: 0.00 on a layered repo, 0.50 and 1.00 on feature-shaped ones.
        """
        named = {name for name in scope_names if name}
        if not named:
            return False
        owner = self.owner_by_word()
        machinery = {word.casefold() for word in self.machinery}
        ubiquitous = ubiquitous_words(named)
        matched = sum(1 for name in named if _distinctive_word(name, machinery, ubiquitous) in owner)
        return matched / len(named) >= SCOPES_ARE_COMPONENTS_RATIO


@dataclass(frozen=True)
class NamePartition:
    """An assignment plus the evidence for how far to trust it."""

    assignment: dict[str, str]
    placed: int
    total: int
    by_structure: bool

    @property
    def coverage(self) -> float:
        """Fraction of units the vocabulary could place.

        Low coverage is the signal that a repo has no domain vocabulary to cluster on -- a
        compiler whose every noun is Node, Visitor and Context. Callers must surface it
        rather than let ``Infrastructure`` quietly swallow the repo.
        """
        return self.placed / self.total if self.total else 0.0


@dataclass(frozen=True)
class Unit:
    """One thing to place: a leaf cluster, or a single symbol."""

    files: tuple[str, ...]
    qualified_names: tuple[str, ...]


def partition_by_name(
    units: dict[str, Unit],
    model: NamingModel,
    *,
    delimiter: str = ".",
    repo_root: str = "",
) -> NamePartition:
    """Assign each unit to a component, by whichever half of its names carries the architecture."""
    scope_names = {scope_of(path, repo_root) for unit in units.values() for path in unit.files}
    if model.scopes_are_components(scope_names):
        return _by_scope(units, model, scope_names, repo_root)
    return _by_vocabulary(units, model, delimiter)


def file_leaf_clusters(graph: CallGraph) -> ClusterResult:
    """One leaf cluster per file, for a partition that groups on names.

    Why not Leiden's clusters: they are drawn from the call graph and cross the boundaries a
    name partition wants to keep. Measured on Beacon, 7 of 21 held symbols from more than one
    component, which caps *any* grouping over them at 0.34 against a ruler the same names
    reach 0.90 on. A file crosses no boundary in either ruler, and pools its identifiers, so
    it carries more vocabulary than a lone symbol does.
    """
    clusters: dict[ClusterId, set[str]] = {}
    cluster_to_files: dict[ClusterId, set[str]] = {}
    file_to_clusters: dict[str, set[ClusterId]] = {}
    for cluster_id, file_path in enumerate(sorted({node.file_path for node in graph.nodes.values() if node.file_path})):
        members = {name for name, node in graph.nodes.items() if node.file_path == file_path}
        clusters[cluster_id] = members
        cluster_to_files[cluster_id] = {file_path}
        file_to_clusters[file_path] = {cluster_id}
    return ClusterResult(
        clusters=clusters,
        cluster_to_files=cluster_to_files,
        file_to_clusters=file_to_clusters,
        strategy=FILE_STRATEGY,
    )


def group_leaf_clusters(
    cluster_results: Mapping[str, ClusterResult],
    model: NamingModel,
    *,
    delimiter: str = ".",
    repo_root: str = "",
) -> tuple[list[set[ClusterId]], NamePartition]:
    """Group leaf clusters into components by name, for ``GroupingService``.

    Returns groups that are exhaustive over every cluster id and disjoint, which is what
    ``_assign_symbol_members`` asserts. A component the names leave empty is dropped, so a
    scope can never produce a group with nothing in it.
    """
    units: dict[str, Unit] = {}
    for result in cluster_results.values():
        for cluster_id, qualified_names in result.clusters.items():
            units[str(cluster_id)] = Unit(
                files=tuple(sorted(result.cluster_to_files.get(cluster_id, set()))),
                qualified_names=tuple(sorted(qualified_names)),
            )

    partition = partition_by_name(units, model, delimiter=delimiter, repo_root=repo_root)
    by_component: dict[str, set[ClusterId]] = {}
    for unit_id, component in partition.assignment.items():
        by_component.setdefault(component, set()).add(int(unit_id))
    return [members for _, members in sorted(by_component.items()) if members], partition


def _by_scope(units: dict[str, Unit], model: NamingModel, scope_names: set[str], repo_root: str = "") -> NamePartition:
    """Group by the scope a unit sits in, merging scopes that share their own word.

    The scope is a grouping key, not a vocabulary lookup: a scope the model never enumerated
    is still a component. Scopes merge on their own distinctive word rather than on a
    component's ``owns``, which keeps ``Ordering.API``, ``Ordering.Domain`` and
    ``OrderProcessor`` together while leaving ``PaymentProcessor`` alone -- letting ``owns``
    do it instead pulled payments into ordering and the web app into the mobile client.
    """
    machinery = {word.casefold() for word in model.machinery}
    ubiquitous = ubiquitous_words(scope_names)
    assignment: dict[str, str] = {}
    placed = 0

    for unit_id, unit in units.items():
        scopes = Counter(s for path in unit.files if (s := scope_of(path, repo_root)))
        if not scopes:
            assignment[unit_id] = INFRASTRUCTURE
            continue
        scope = max(sorted(scopes), key=lambda name: (scopes[name], name))
        assignment[unit_id] = _distinctive_word(scope, machinery, ubiquitous) or scope
        placed += 1

    return NamePartition(assignment, placed, len(units), by_structure=True)


def _by_vocabulary(units: dict[str, Unit], model: NamingModel, delimiter: str) -> NamePartition:
    """Group by the domain words the identifiers carry."""
    owner = model.owner_by_word()
    machinery = {word.casefold() for word in model.machinery}
    assignment: dict[str, str] = {}
    placed = 0

    for unit_id, unit in units.items():
        votes: Counter[str] = Counter()
        for qualified_name in unit.qualified_names:
            for part in _segments(qualified_name, delimiter):
                words = tokenize(part)
                for position, word in enumerate(words):
                    key = stem(word)
                    if word.casefold() in machinery or key in machinery or key not in owner:
                        continue
                    # A noun phrase modifies rightwards, so a word nearer the head weighs
                    # more. That reads `IncidentResolvedMetricsHandler` as metrics: it
                    # reacts to the incident event rather than being about incidents.
                    votes[owner[key]] += 2.0 ** (position - (len(words) - 1))
        if votes:
            assignment[unit_id] = max(sorted(votes), key=lambda name: (votes[name], name))
            placed += 1
        else:
            assignment[unit_id] = INFRASTRUCTURE

    return NamePartition(assignment, placed, len(units), by_structure=False)


def _distinctive_word(scope_name: str, machinery: set[str], ubiquitous: frozenset[str]) -> str:
    """The first word of a scope name that names this scope rather than every scope."""
    for word in tokenize(scope_name):
        folded = stem(word)
        if word.casefold() not in machinery and folded not in machinery and folded not in ubiquitous:
            return folded
    return ""


def _segments(qualified_name: str, delimiter: str) -> list[str]:
    """Split on *delimiter*, keeping parameter lists and generics whole."""
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
