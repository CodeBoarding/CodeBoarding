"""One pure function from units and a scope's rules to a partition.

A unit's box depends on its own names and the rules alone, never on a statistic over the
collection, so a file added beside it cannot move it. Order of trial: the longest matching
prefix, then a weighted vote over the words the rules own, then the longest matching
fallback prefix; what is left is unplaced and reported.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field

from static_analyzer.clustering.names.inventory import Unit
from static_analyzer.clustering.names.spec import UNPLACED, Prefix, ScopeSpec
from static_analyzer.clustering.names.tokens import segments, stem, tokenize
from static_analyzer.config import ClusteringConfig

PREFIX = "prefix"
TERM = "term"
FALLBACK = "fallback"


@dataclass
class Partition:
    scope_id: str
    assignment: dict[str, str] = field(default_factory=dict)
    """unit_id -> component_id, for every unit some rule claimed."""
    members: dict[str, list[Unit]] = field(default_factory=dict)
    """component_id -> its units, in the scope's rule order; the unplaced bucket included."""
    placed_by: dict[str, str] = field(default_factory=dict)
    """unit_id -> how it was placed: ``prefix``, ``term``, ``fallback`` or ``unplaced``."""
    unplaced: list[Unit] = field(default_factory=list)
    """Units no rule claimed, whether or not the scope has a bucket to draw them in."""
    new_scopes: dict[Prefix, list[Unit]] = field(default_factory=dict)
    """Units no named prefix claimed, whether a word, a fallback or nothing placed them, by the
    prefix at which their position leaves every prefix a rule owns. A group of two or more
    whose units are all new to the analysis is a new scope; a group under a prefix some rule
    already falls back to is that rule's residue. Why words do not exempt a unit: a new
    directory whose names happen to carry a word some rule owns is still a new directory."""

    def size(self, component_id: str) -> int:
        return len(self.members.get(component_id, ()))


def replay(units: Iterable[Unit], scope: ScopeSpec, role_words: frozenset[str]) -> Partition:
    partition = Partition(scope.scope_id, members={rule.component_id: [] for rule in scope.rules})
    components = scope.components
    rank = {rule.component_id: index for index, rule in enumerate(components)}
    primary = [(prefix, rule.component_id) for rule in components for prefix in rule.prefixes]
    fallback = [(prefix, rule.component_id) for rule in components for prefix in rule.fallback_prefixes]
    owner_by_term: dict[str, str] = {}
    for rule in components:
        for term in rule.terms:
            owner_by_term.setdefault(term, rule.component_id)
    # An empty prefix (a root drawn as one box) claims every position; it must not hide the
    # directories added after it, so only a named prefix settles a unit.
    named = [(prefix, component_id) for prefix, component_id in primary if prefix]
    known = {prefix for prefix, _ in named}
    bucket = scope.unplaced_rule
    for unit in units:
        component_id, how = _place(unit, primary, fallback, owner_by_term, rank, role_words)
        if how != PREFIX or _longest_match(unit.position, named) is None:
            partition.new_scopes.setdefault(divergence(unit.position, known), []).append(unit)
        if component_id is None:
            partition.unplaced.append(unit)
            partition.placed_by[unit.unit_id] = UNPLACED
            if bucket is None:
                continue
            component_id = bucket.component_id
        partition.assignment[unit.unit_id] = component_id
        partition.members[component_id].append(unit)
        partition.placed_by[unit.unit_id] = how
    return partition


def term_votes(unit: Unit, owner_by_term: dict[str, str], role_words: frozenset[str]) -> Counter[str]:
    """Weighted votes of a unit's words for the rules that own them.

    Why the weighting: a noun phrase modifies rightwards, so a word nearer the head weighs
    more; ``IncidentResolvedMetricsHandler`` is about metrics and reacts to an incident.
    """
    votes: Counter[str] = Counter()
    for qualified_name in unit.names:
        for part in segments(qualified_name, ClusteringConfig.QUALIFIED_NAME_DELIMITER):
            words = tokenize(part)
            for position, word in enumerate(words):
                key = stem(word)
                if key in role_words:
                    continue
                owner = owner_by_term.get(key)
                if owner is not None:
                    votes[owner] += 2.0 ** (position - (len(words) - 1))
    return votes


def _place(
    unit: Unit,
    primary: list[tuple[Prefix, str]],
    fallback: list[tuple[Prefix, str]],
    owner_by_term: dict[str, str],
    rank: dict[str, int],
    role_words: frozenset[str],
) -> tuple[str | None, str]:
    owner = _longest_match(unit.position, primary)
    if owner is not None:
        return owner, PREFIX
    if owner_by_term:
        votes = term_votes(unit, owner_by_term, role_words)
        if votes:
            # Ties go to the rule that comes first in the scope, never to the id's spelling.
            return max(votes, key=lambda component_id: (votes[component_id], -rank[component_id])), TERM
    owner = _longest_match(unit.position, fallback)
    if owner is not None:
        return owner, FALLBACK
    return None, UNPLACED


def _longest_match(position: Prefix, prefixes: list[tuple[Prefix, str]]) -> str | None:
    best: str | None = None
    best_length = -1
    for prefix, component_id in prefixes:
        if len(prefix) > best_length and position[: len(prefix)] == prefix:
            best, best_length = component_id, len(prefix)
    return best


def divergence(position: Prefix, known: set[Prefix]) -> Prefix:
    """The first prefix of *position* that no known prefix passes through."""
    ancestors = {prefix[:length] for prefix in known for length in range(len(prefix) + 1)}
    for length in range(1, len(position) + 1):
        if position[:length] not in ancestors:
            return position[:length]
    return position
