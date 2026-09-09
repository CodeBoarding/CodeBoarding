"""The ladder: a scope's rules drafted one rung at a time, the grouper its one judgement."""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import Protocol

from clustering_ids import ROOT_SCOPE_ID, ScopeId
from static_analyzer.clustering.names.frontier import BOX, FILE, HEAD, LOOSE, RESIDUAL, WORD, Candidate, walk
from static_analyzer.clustering.names.inventory import Trie, Unit
from static_analyzer.clustering.names.replay import Partition, replay
from static_analyzer.clustering.names.spec import (
    FILES,
    FRONTIER,
    ISLAND,
    LAYERS,
    LEAF,
    ROLE,
    SEGMENT,
    UNMERGE,
    UNPLACED,
    ComponentRule,
    Prefix,
    ScopeSpec,
    TreeSpec,
    is_root,
)
from static_analyzer.clustering.names.tokens import (
    ROLE_WORDS,
    distinctive_word,
    segments,
    stem,
    stems,
    tokenize,
    ubiquitous_words,
)
from static_analyzer.config import ClusteringConfig

MIN_UNITS = 2
GUARD_SHARE = 0.05
LEAF_UNITS = 7
"""Units at or under which a component is a leaf: a box a reader takes in at a glance."""
LEAF_CAP = 135
"""Units above which a component transposes a layered sub-tree."""
BUDGET = 9
"""Components a scope is folded toward along its links, and only along links that carry
at least half of what the folded candidate exchanges; the guard, not the budget, is what
a rung must clear."""
LIMIT = 15
"""Components a scope never exceeds: past the budget nothing folds into a hub, past the
limit the names' vocabulary and, last, a pool of the smallest bring it down."""
MIN_LINKS = 2
"""Graph links two candidates must exchange before they count as affine: one is noise."""
HUB_SHARE = 0.4
"""Share of its siblings that must call a candidate for it to be shared infrastructure: a
hub neither absorbs a sibling nor folds into one, whatever the counts say."""
MIN_HUB_PARTNERS = 3
"""Siblings a hub is called by at the least, so a scope of three cannot hold one."""
MIN_HUB_UNITS = 3
"""Units a hub needs to be drawn on its own; a smaller one is a loose file everybody calls."""
CAP_SHARE = 0.6
"""No fold may grow a component past this share of its scope."""
UNPLACED_NAME = "Unassigned"
LOOSE_NAME = "Loose files"
OTHER_NAME = "Other files"
ISLAND_SHARE = 1 / 3
"""Share of a scope one family must hold to be drawn on its own against the rest."""
ISLAND_STRAYS = 1
"""Links a family may exchange with the rest and still count as an island: one is noise."""
CONSUMER_WORDS = frozenset(
    {"test", "spec", "e2e", "sample", "example", "doc", "bench", "benchmark", "snippet", "demo", "script", "cypress"}
)
"""Stems naming code that calls the scope without being part of it: it owns no helper."""

Links = Mapping[tuple[str, str], int]
"""Weight of the graph edges from one unit to another, keyed ``(calling unit, called unit)``."""


@dataclass(frozen=True)
class GroupingContext:
    """What a grouper may know beyond the candidates: the scope, and per candidate its size,
    a few identifiers and the graph links it exchanges with each sibling, so a grouper can
    judge without ever seeing a unit."""

    scope_id: ScopeId
    role_words: frozenset[str]
    unit_count: int
    rung: str
    sizes: dict[str, int] = field(default_factory=dict)
    samples: dict[str, tuple[str, ...]] = field(default_factory=dict)
    links: dict[tuple[str, str], int] = field(default_factory=dict)
    """Graph edges between two candidates' units, keyed by their keys in sorted order."""
    calls: dict[tuple[str, str], int] = field(default_factory=dict)
    """The same edges with their direction kept: ``(calling candidate, called candidate)``."""
    vocabulary: dict[str, Counter[str]] = field(default_factory=dict)
    """Per candidate, the stems its units' names are made of, counted once per unit."""
    projects: frozenset[str] = frozenset()
    """Candidates whose directory is a project of its own (a manifest sits in it)."""
    floor: int = MIN_UNITS
    """Units a candidate must hold to stand on its own in this scope."""


@dataclass(frozen=True)
class CandidateGroup:
    """Candidates one component is made of. ``terms`` are words the group owns beyond theirs."""

    name: str
    keys: tuple[str, ...]
    terms: tuple[str, ...] = ()


class Grouper(Protocol):
    """Turns the candidates of one rung into named components: the one judgement in the tree.

    A grouper sees candidates, never units, so a wrong answer can only merge boxes. Every
    candidate key must land in exactly one group.
    """

    name: str

    def group(self, candidates: Sequence[Candidate], context: GroupingContext) -> list[CandidateGroup]: ...


class KinshipGrouper:
    """Merge candidates that share their distinctive word: ``Ordering`` with ``OrderProcessor``."""

    name = "kinship"

    def group(self, candidates: Sequence[Candidate], context: GroupingContext) -> list[CandidateGroup]:
        if context.rung == UNMERGE:
            # The parts of a fold are past kinship: merging them again would undo the un-merge.
            return [CandidateGroup(candidate_name(candidate), (candidate.key,)) for candidate in candidates]
        ubiquitous = ubiquitous_words(candidate.label for candidate in candidates if candidate.label)
        by_word: dict[str, list[Candidate]] = {}
        solo: list[Candidate] = []
        for candidate in candidates:
            word = distinctive_word(candidate.label, context.role_words, ubiquitous) if candidate.label else ""
            if word:
                by_word.setdefault(word, []).append(candidate)
            else:
                solo.append(candidate)
        groups = [
            CandidateGroup(_plainest(members), tuple(candidate.key for candidate in members), (word,))
            for word, members in by_word.items()
        ]
        groups.extend(CandidateGroup(candidate_name(candidate), (candidate.key,)) for candidate in solo)
        return groups


class AffinityGrouper:
    """Kinship, then the graph, in four steps that read the same at every depth.

    1. Every small candidate is placed by its role: a hub or an application stays, a helper
       (called by one sibling only) goes inside that sibling, one shared by exactly two goes
       into the larger of them, one nobody links to goes to the loose files.
    2. Over the budget, the smallest candidate joins the sibling it exchanges the most links
       with, never a hub, until nothing affine is left.
    3. Over the limit still, the two candidates whose names share the most vocabulary merge.
    4. Over the limit still, the smallest are pooled into one box, named for what they are.

    Why hubs are out of every fold: shared infrastructure (an event bus, a ``core`` package)
    is what every small sibling links to most, so by counts every service belongs to it; a
    reader wants the infrastructure drawn as one box and the services that use it as theirs.
    """

    name = "affinity"

    def group(self, candidates: Sequence[Candidate], context: GroupingContext) -> list[CandidateGroup]:
        fold = _Fold(KinshipGrouper().group(candidates, context), candidates, context)
        fold.place()
        fold.toward_budget()
        fold.by_vocabulary()
        fold.pool()
        return fold.groups()


class _Fold:
    """The merges of one rung, over the candidates' sizes, links and calls."""

    def __init__(self, groups: list[CandidateGroup], candidates: Sequence[Candidate], context: GroupingContext):
        self.context = context
        self.by_key = {candidate.key: candidate for candidate in candidates}
        self.members = [list(group.keys) for group in groups]
        self.terms = [group.terms for group in groups]
        self.sizes = [sum(context.sizes.get(key, 0) for key in group.keys) for group in groups]
        count = len(groups)
        self.links = [
            [self._between(left, right, context.links, ordered=False) for right in self.members]
            for left in self.members
        ]
        self.calls = [
            [self._between(left, right, context.calls, ordered=True) for right in self.members] for left in self.members
        ]
        for index in range(count):
            self.links[index][index] = self.calls[index][index] = 0
        self.vocabulary = [
            sum((context.vocabulary.get(key, Counter()) for key in group.keys), Counter()) for group in groups
        ]
        self.floor = context.floor
        live = self.live()
        threshold = max(MIN_HUB_PARTNERS, HUB_SHARE * (len(live) - 1))
        self.hubs = {i for i in live if sum(1 for j in live if self.calls[j][i] >= MIN_LINKS) >= threshold}
        self.projects = {i for i, keys in enumerate(self.members) if any(key in context.projects for key in keys)}
        self.consumers = {i for i in live if self._is_consumer(i)}
        self.kept: set[int] = set()
        self.loose = next(
            (i for i, keys in enumerate(self.members) if any(self.by_key[k].kind == LOOSE for k in keys)), None
        )

    @staticmethod
    def _between(left: list[str], right: list[str], weights: Mapping[tuple[str, str], int], *, ordered: bool) -> int:
        if ordered:
            return sum(weights.get((a, b), 0) for a in left for b in right)
        return sum(weights.get((min(a, b), max(a, b)), 0) for a in left for b in right)

    def _is_consumer(self, index: int) -> bool:
        """Loose files, tests, samples, benches and docs call the scope without being part of it."""
        if any(self.by_key[key].kind == LOOSE for key in self.members[index]):
            return True
        labels = (self.by_key[key].label for key in self.members[index])
        return any(set(stems(label)) & CONSUMER_WORDS for label in labels)

    def live(self) -> list[int]:
        return sorted((i for i in range(len(self.members)) if self.sizes[i]), key=lambda i: (self.sizes[i], i))

    def merge(self, source: int, target: int) -> None:
        self.members[target].extend(self.members[source])
        self.terms[target] = _dedupe(self.terms[target] + self.terms[source])
        self.sizes[target] += self.sizes[source]
        self.sizes[source] = 0
        self.vocabulary[target].update(self.vocabulary[source])
        for other in range(len(self.members)):
            self.links[target][other] += self.links[source][other]
            self.links[other][target] += self.links[other][source]
            self.calls[target][other] += self.calls[source][other]
            self.calls[other][target] += self.calls[other][source]
            self.links[source][other] = self.links[other][source] = 0
            self.calls[source][other] = self.calls[other][source] = 0
        self.links[target][target] = self.calls[target][target] = 0
        self.members[source] = []
        if source in self.hubs:
            self.hubs.add(target)
        if source in self.projects:
            self.projects.add(target)

    def place(self) -> None:
        """Every candidate under the floor, by its role in the calls."""
        for index in self.live():
            if self.sizes[index] >= self.floor:
                continue
            # A hub calling a small sibling makes it a subscriber, not a helper: the bus owns no service.
            callers = {
                j: self.calls[j][index]
                for j in self.live()
                if j != index and j not in self.consumers and j not in self.hubs and self.calls[j][index]
            }
            incoming = sum(callers.values())
            outgoing = sum(self.calls[index][j] for j in self.live() if j != index)
            if index in self.hubs:
                if self.sizes[index] < MIN_HUB_UNITS and self.loose is not None and self.loose != index:
                    self.merge(index, self.loose)
                else:
                    self.kept.add(index)
            elif index in self.projects or len(callers) >= 3:
                self.kept.add(index)
            elif incoming >= MIN_LINKS and len(callers) == 1:
                self.merge(index, next(iter(callers)))
            elif incoming >= MIN_LINKS and len(callers) == 2:
                # The larger caller, unless taking the helper would grow it past the cap.
                cap = CAP_SHARE * self.context.unit_count
                fitting = [j for j in callers if self.sizes[j] + self.sizes[index] <= cap] or list(callers)
                self.merge(index, max(fitting, key=lambda j: (self.sizes[j], -j)))
            elif incoming or outgoing >= MIN_LINKS:
                self.kept.add(index)
            elif self.loose is not None and self.loose != index:
                self.merge(index, self.loose)
            else:
                self.kept.add(index)

    def toward_budget(self) -> None:
        """The smallest candidate joins the sibling it exchanges the most links with, never a hub.

        Below the floor a candidate the placement left standing joins whoever it links to;
        over the budget a candidate joins only a sibling carrying at least half of its links,
        so a real component is never folded on the two links a helper brought along.
        """
        cap = CAP_SHARE * self.context.unit_count
        while True:
            live = self.live()
            over_budget = len(live) > BUDGET
            sources = live if over_budget else [i for i in live if self.sizes[i] < self.floor and i not in self.kept]
            chosen = None
            for source in sources:
                if source in self.hubs:
                    continue
                degree = sum(self.links[source][other] for other in live)
                best: tuple[int, int, int] | None = None
                for target in live:
                    count = self.links[source][target]
                    if target == source or target in self.hubs or count < MIN_LINKS:
                        continue
                    if self.sizes[source] + self.sizes[target] > cap:
                        continue
                    if over_budget and self.sizes[source] >= self.floor and count * 2 < degree:
                        continue
                    if best is None or (count, -self.sizes[target], -target) > best:
                        best = (count, -self.sizes[target], -target)
                if best is not None:
                    chosen = (source, -best[2])
                    break
            if chosen is None:
                return
            self.merge(*chosen)

    def by_vocabulary(self) -> None:
        """Over the limit, the two non-hub candidates whose names share the most vocabulary merge."""
        while len(self.live()) > LIMIT:
            live = [i for i in self.live() if i not in self.hubs]
            vectors = self._tfidf(live)
            best: tuple[float, int, int] | None = None
            for a in range(len(live)):
                for b in range(a + 1, len(live)):
                    i, j = live[a], live[b]
                    similarity = sum(weight * vectors[j].get(word, 0.0) for word, weight in vectors[i].items())
                    if similarity > 0 and (best is None or similarity > best[0]):
                        best = (similarity, i, j)
            if best is None:
                return
            _, i, j = best
            self.merge(j if self.sizes[i] >= self.sizes[j] else i, i if self.sizes[i] >= self.sizes[j] else j)

    def _tfidf(self, live: list[int]) -> dict[int, dict[str, float]]:
        frequency: Counter[str] = Counter()
        for index in live:
            frequency.update(self.vocabulary[index].keys())
        vectors: dict[int, dict[str, float]] = {}
        for index in live:
            total = sum(self.vocabulary[index].values()) or 1
            weights = {
                word: (count / total) * math.log(1 + len(live) / frequency[word])
                for word, count in self.vocabulary[index].items()
                if frequency[word] < 0.8 * len(live)
            }
            norm = math.sqrt(sum(weight * weight for weight in weights.values())) or 1.0
            vectors[index] = {word: weight / norm for word, weight in weights.items()}
        return vectors

    def pool(self) -> None:
        """Over the limit still, the smallest non-hub candidates become one box."""
        live = [i for i in self.live() if i not in self.hubs]
        excess = len(self.live()) - LIMIT
        if excess <= 0 or len(live) < 2:
            return
        pooled = live[: excess + 1]
        target = pooled[-1]
        for source in pooled[:-1]:
            self.merge(source, target)
        self.pooled = target

    pooled: int | None = None

    def groups(self) -> list[CandidateGroup]:
        groups = []
        for index, keys in enumerate(self.members):
            if not keys:
                continue
            if index == self.pooled:
                groups.append(CandidateGroup(OTHER_NAME, tuple(keys), self.terms[index]))
                continue
            loose = next((self.by_key[key] for key in keys if self.by_key[key].kind == LOOSE), None)
            if loose is not None:
                # What joined the loose files is loose; the box keeps the name a reader knows.
                groups.append(CandidateGroup(candidate_name(loose), tuple(keys), self.terms[index]))
                continue
            # The biggest member names a fold: the box a reader already recognises.
            biggest = max(self.context.sizes.get(key, 0) for key in keys)
            name = _plainest([self.by_key[key] for key in keys if self.context.sizes.get(key, 0) == biggest])
            groups.append(CandidateGroup(name, tuple(keys), self.terms[index]))
        return groups


DETERMINISTIC_GROUPERS: dict[str, type[Grouper]] = {
    KinshipGrouper.name: KinshipGrouper,
    AffinityGrouper.name: AffinityGrouper,
}
"""The groupers a run can construct without a model, by the name a specification records."""


def candidate_name(candidate: Candidate) -> str:
    if candidate.kind == LOOSE:
        where = ".".join(candidate.fallback_prefixes[0]) if candidate.fallback_prefixes else ""
        return f"Loose files in {where}" if where else "Loose files"
    if candidate.kind == RESIDUAL:
        return f"{candidate.label} (residual)"
    if candidate.kind in (WORD, HEAD):
        return candidate.label.capitalize()
    if candidate.kind == BOX and not candidate.label:
        return "All files"
    return candidate.label


def role_words_for(machinery: Iterable[str]) -> frozenset[str]:
    return ROLE_WORDS | frozenset(stem(word) for word in machinery)


def draft_tree(
    units: Iterable[Unit],
    grouper: Grouper,
    max_depth: int,
    *,
    machinery: Iterable[str] = (),
    links: Links | None = None,
) -> TreeSpec:
    """Draft the root and every scope below it down to ``max_depth``.

    ``links`` are the graph's edges between units; they reach the grouper as affinities
    between candidates and never place a unit.
    """
    if max_depth < 1:
        raise ValueError("max_depth must be at least 1")
    machinery = frozenset(machinery)
    spec = TreeSpec(machinery=machinery, grouper=grouper.name)
    role_words = role_words_for(machinery)

    def build(scope_id: ScopeId, scope_units: list[Unit], parts: tuple[ComponentRule, ...], depth: int) -> None:
        scope, partition = draft_scope(scope_id, scope_units, role_words, grouper, parts=parts, links=links)
        spec.set_scope(scope)
        if depth >= max_depth:
            return
        for rule in scope.components:
            build(rule.component_id, partition.members[rule.component_id], rule.parts, depth + 1)

    build(ROOT_SCOPE_ID, list(units), (), 1)
    return spec


def draft_scope(
    scope_id: ScopeId,
    units: Iterable[Unit],
    role_words: frozenset[str],
    grouper: Grouper,
    *,
    parts: tuple[ComponentRule, ...] = (),
    links: Links | None = None,
) -> tuple[ScopeSpec, Partition]:
    """Draft one scope's rules from its units: the first rung that splits it wins.

    The ladder is the same at every depth: the parts a fold merged, the directory frontier,
    the layers of a layered directory, the files, their role words, and an island. The root
    is never refused: a root nothing splits is drawn as the one box the frontier gave it.
    """
    scope_units = list(units)
    links = links or {}
    rungs: list[tuple[str, Callable[[], tuple[list[ComponentRule], str]]]] = []

    def frontier(rung: str, transpose: bool, layers: bool = False) -> tuple[list[ComponentRule], str]:
        return _frontier_rules(scope_id, scope_units, role_words, grouper, rung, links, transpose, layers)

    if len(parts) >= 2:
        rungs.append((UNMERGE, lambda: _unmerge_rules(scope_id, scope_units, parts, role_words, grouper, links)))
    if is_root(scope_id) or len(scope_units) > LEAF_UNITS:
        transpose = is_root(scope_id) or len(scope_units) > LEAF_CAP
        rungs.append((FRONTIER if is_root(scope_id) else SEGMENT, lambda: frontier(SEGMENT, transpose)))
        rungs.append((LAYERS, lambda: frontier(LAYERS, False, layers=True)))
        rungs.append((FILES, lambda: _file_rules(scope_id, scope_units, role_words, grouper, links)))
        rungs.append((ROLE, lambda: _role_rules(scope_id, scope_units, role_words, grouper, links)))
        rungs.append((ISLAND, lambda: _island_rules(scope_id, scope_units, role_words, grouper, links)))
    produced: dict[str, tuple[list[ComponentRule], str]] = {}
    for rung, produce in rungs:
        produced[rung] = rules, axis = produce()
        settled = _settle(scope_id, scope_units, rules, role_words, rung, guard=rung != FRONTIER)
        if settled is None:
            continue
        scope, partition = settled
        scope.axis = axis
        return scope, partition
    if is_root(scope_id) and scope_units:
        rules, axis = produced[FRONTIER]
        settled = _settle(scope_id, scope_units, rules, role_words, FRONTIER, guard=False, min_rules=1)
        if settled is not None:
            scope, partition = settled
            scope.axis = axis
            return scope, partition
    return ScopeSpec(
        scope_id, rung=LEAF, leaf_reason=_leaf_reason(scope_id, len(scope_units), parts, rungs)
    ), Partition(scope_id)


def _leaf_reason(
    scope_id: ScopeId, unit_count: int, parts: tuple[ComponentRule, ...], rungs: Sequence[tuple[str, object]]
) -> str:
    if is_root(scope_id):
        return f"no rung ({', '.join(rung for rung, _ in rungs)}) yields two children of {unit_count} units"
    unmerge = "un-merge failed the guard" if len(parts) >= 2 else "nothing to un-merge"
    if unit_count <= LEAF_UNITS:
        return f"small: {unit_count} units, at most {LEAF_UNITS}; {unmerge}"
    tried = ", ".join(rung for rung, _ in rungs if rung != UNMERGE)
    kind = "cohesive" if unit_count <= LEAF_CAP else "exhausted"
    return f"{kind}: {unit_count} units; {unmerge}; no rung ({tried}) yields two children"


def _unmerge_rules(
    scope_id: ScopeId,
    units: list[Unit],
    parts: tuple[ComponentRule, ...],
    role_words: frozenset[str],
    grouper: Grouper,
    links: Links,
) -> tuple[list[ComponentRule], str]:
    """The candidates a grouping merged, offered to the grouper again in the scope they now share.

    Why through the grouper: the fold that merged them answered for the parent's budget; a
    scope of seventy parts is over its own, and folds toward it along its own links. A residual
    part is offered by its bare label, since ``candidate_name`` appends the suffix again.
    """
    candidates = [
        Candidate(
            f"part:{index}:{part.name}",
            part.origin,
            part.name.removesuffix(" (residual)") if part.origin == RESIDUAL else part.name,
            part.prefixes,
            part.fallback_prefixes,
            part.terms,
        )
        for index, part in enumerate(parts)
    ]
    context = _context(scope_id, units, candidates, role_words, UNMERGE, links)
    return _rules_from_groups(grouper.group(candidates, context), candidates), "structural"


def _frontier_rules(
    scope_id: ScopeId,
    units: list[Unit],
    role_words: frozenset[str],
    grouper: Grouper,
    rung: str,
    links: Links,
    transpose: bool,
    layers: bool = False,
) -> tuple[list[ComponentRule], str]:
    frontier = walk(Trie(units), role_words, transpose=transpose, layers=layers)
    candidates = sorted(frontier.candidates, key=lambda candidate: candidate.key)
    if not candidates:
        return [], frontier.axis
    context = _context(scope_id, units, candidates, role_words, rung, links)
    return _rules_from_groups(grouper.group(candidates, context), candidates), frontier.axis


def _file_rules(
    scope_id: ScopeId,
    units: list[Unit],
    role_words: frozenset[str],
    grouper: Grouper,
    links: Links,
) -> tuple[list[ComponentRule], str]:
    """One candidate per file, labelled by its own name: kinship on the names, then the fold along the graph."""
    by_key: dict[Prefix, list[Unit]] = {}
    for unit in units:
        by_key.setdefault(unit.key, []).append(unit)
    candidates = [Candidate(f"{FILE}:{'.'.join(key)}", FILE, _label(key), prefixes=(key,)) for key in sorted(by_key)]
    return _grouped_rules(scope_id, units, candidates, role_words, grouper, FILES, links), FILES


def _role_rules(
    scope_id: ScopeId,
    units: list[Unit],
    role_words: frozenset[str],
    grouper: Grouper,
    links: Links,
) -> tuple[list[ComponentRule], str]:
    """One candidate per head word of the files' names (``Strategy``, ``Options``, ``Converter``).

    Why here and nowhere else: a role word never defines a box above a leaf, but inside a
    feature the roles are the structure there is.
    """
    by_head: dict[str, list[Unit]] = {}
    for unit in units:
        words = stems(_label(unit.key))
        if words:
            by_head.setdefault(words[-1], []).append(unit)
    candidates = [
        Candidate(
            f"{HEAD}:{word}",
            HEAD,
            word,
            prefixes=tuple(dict.fromkeys(unit.key for unit in members)),
            terms=(word,),
        )
        for word, members in sorted(by_head.items())
    ]
    return _grouped_rules(scope_id, units, candidates, role_words, grouper, ROLE, links), ROLE


def _island_rules(
    scope_id: ScopeId,
    units: list[Unit],
    role_words: frozenset[str],
    grouper: Grouper,
    links: Links,
) -> tuple[list[ComponentRule], str]:
    """One family that talks to itself and to nobody else, against the rest of a fan.

    The files rung wants two families; a fan of parallel implementations usually has one at
    most, the files that route through a shared sibling, beside siblings that share nothing.
    That family is a box only when no link crosses to the rest: a family the rest calls is
    the fold cutting a hub's neighbours in two, which is arbitrary, and stays whole.
    """
    by_key: dict[Prefix, list[Unit]] = {}
    for unit in units:
        by_key.setdefault(unit.key, []).append(unit)
    candidates = [Candidate(f"{FILE}:{'.'.join(key)}", FILE, _label(key), prefixes=(key,)) for key in sorted(by_key)]
    if len(candidates) < 2:
        return [], ISLAND
    context = _context(scope_id, units, candidates, role_words, ISLAND, links)
    groups = grouper.group(candidates, context)
    strong = [group for group in groups if sum(context.sizes.get(key, 0) for key in group.keys) >= context.floor]
    if len(strong) != 1:
        return [], ISLAND
    family_keys = set(strong[0].keys)
    family = [unit for unit in units if f"{FILE}:{'.'.join(unit.key)}" in family_keys]
    rest = [unit for unit in units if f"{FILE}:{'.'.join(unit.key)}" not in family_keys]
    if len(family) < ISLAND_SHARE * len(units) or len(rest) < context.floor:
        return [], ISLAND
    family_ids = {unit.unit_id for unit in family}
    rest_ids = {unit.unit_id for unit in rest}
    crossing = sum(
        weight
        for (left, right), weight in links.items()
        if (left in family_ids and right in rest_ids) or (left in rest_ids and right in family_ids)
    )
    if crossing > ISLAND_STRAYS:
        return [], ISLAND
    rules = _rules_from_groups(strong, [candidate for candidate in candidates if candidate.key in family_keys])
    rules.append(
        ComponentRule(
            "",
            _rest_name(rest),
            prefixes=tuple(dict.fromkeys(unit.key for unit in rest)),
            fallback_prefixes=(_common_prefix(units),),
            origin=ISLAND,
        )
    )
    return rules, ISLAND


def _rest_name(units: list[Unit]) -> str:
    """``Other converters``: the word most of the fan's names end in, else ``Other files``."""
    heads: Counter[str] = Counter()
    for unit in units:
        words = tokenize(_label(unit.key))
        if words:
            heads[words[-1].casefold()] += 1
    if not heads:
        return "Other files"
    word, count = heads.most_common(1)[0]
    if count < len(units) / 2:
        return "Other files"
    return f"Other {word}" if word.endswith("s") else f"Other {word}s"


def _grouped_rules(
    scope_id: ScopeId,
    units: list[Unit],
    candidates: list[Candidate],
    role_words: frozenset[str],
    grouper: Grouper,
    rung: str,
    links: Links,
) -> list[ComponentRule]:
    """Group the candidates; a group under the floor pools into one loose bucket, never a one-file box."""
    if len(candidates) < 2:
        return []
    context = _context(scope_id, units, candidates, role_words, rung, links)
    groups = grouper.group(candidates, context)
    strong = [group for group in groups if sum(context.sizes.get(key, 0) for key in group.keys) >= context.floor]
    if len(strong) < 2:
        return []
    kept = {key for group in strong for key in group.keys}
    rules = _rules_from_groups(strong, [candidate for candidate in candidates if candidate.key in kept])
    if len(strong) < len(groups):
        rules.append(ComponentRule("", LOOSE_NAME, fallback_prefixes=(_common_prefix(units),), origin=LOOSE))
    return rules


def _label(key: Prefix) -> str:
    return key[-1] if key else ""


def _common_prefix(units: list[Unit]) -> Prefix:
    shared: list[str] = []
    for parts in zip(*(unit.position for unit in units)):
        if len(set(parts)) != 1:
            break
        shared.append(parts[0])
    return tuple(shared)


SAMPLE_IDENTIFIERS = 6


def _context(
    scope_id: ScopeId,
    units: list[Unit],
    candidates: Sequence[Candidate],
    role_words: frozenset[str],
    rung: str,
    links: Links,
) -> GroupingContext:
    """Size, a few identifiers, vocabulary, calls and projects per candidate, from a replay of the candidates as rules."""
    provisional = ScopeSpec(
        scope_id,
        [replace(_candidate_rule(candidate), component_id=candidate.key) for candidate in candidates],
        rung=rung,
    )
    partition = replay(units, provisional, role_words)
    between: Counter[tuple[str, str]] = Counter()
    calls: Counter[tuple[str, str]] = Counter()
    for (left, right), weight in links.items():
        left_owner, right_owner = partition.assignment.get(left), partition.assignment.get(right)
        if left_owner and right_owner and left_owner != right_owner:
            between[(min(left_owner, right_owner), max(left_owner, right_owner))] += weight
            calls[(left_owner, right_owner)] += weight
    samples: dict[str, tuple[str, ...]] = {}
    vocabulary: dict[str, Counter[str]] = {}
    projects: set[str] = set()
    for candidate in candidates:
        seen: dict[str, None] = {}
        words: Counter[str] = Counter()
        for unit in partition.members.get(candidate.key, []):
            if unit.project and unit.position in candidate.prefixes:
                projects.add(candidate.key)
            unit_words: set[str] = set()
            for name in unit.names:
                parts = segments(name, ClusteringConfig.QUALIFIED_NAME_DELIMITER)
                seen.setdefault(parts[-1], None)
                for part in parts:
                    unit_words.update(stems(part))
            words.update(unit_words)
        samples[candidate.key] = tuple(list(seen)[:SAMPLE_IDENTIFIERS])
        vocabulary[candidate.key] = words
    sizes = {candidate.key: partition.size(candidate.key) for candidate in candidates}
    return GroupingContext(
        scope_id,
        role_words,
        len(units),
        rung,
        sizes,
        samples,
        dict(between),
        dict(calls),
        vocabulary,
        frozenset(projects),
        _floor(len(units)),
    )


def _plainest(members: list[Candidate]) -> str:
    """``Ordering.API`` over ``OrderProcessor``: the member whose label says the least beyond the group's word.

    Fewest words that are not role words, then fewest words, then the alphabet.
    """

    def plainness(candidate: Candidate) -> tuple[int, int, str]:
        words = tokenize(candidate.label)
        return (sum(1 for word in words if stem(word) not in ROLE_WORDS), len(words), candidate.label)

    return candidate_name(min(members, key=plainness))


def _rules_from_groups(groups: list[CandidateGroup], candidates: Sequence[Candidate]) -> list[ComponentRule]:
    by_key = {candidate.key: candidate for candidate in candidates}
    seen: Counter[str] = Counter(key for group in groups for key in group.keys)
    missing = sorted(set(by_key) - set(seen))
    unknown = sorted(set(seen) - set(by_key))
    repeated = sorted(key for key, count in seen.items() if count > 1)
    empty = [group.name for group in groups if not group.keys]
    if missing or unknown or repeated or empty:
        raise ValueError(
            "grouping must cover every candidate once and name no empty group: "
            f"missing={missing} unknown={unknown} repeated={repeated} empty={empty}"
        )
    rules: list[ComponentRule] = []
    for group in groups:
        members = [by_key[key] for key in group.keys]
        rules.append(
            ComponentRule(
                component_id="",
                name=group.name,
                prefixes=tuple(prefix for candidate in members for prefix in candidate.prefixes),
                terms=_dedupe(group.terms + tuple(term for candidate in members for term in candidate.terms)),
                fallback_prefixes=tuple(prefix for candidate in members for prefix in candidate.fallback_prefixes),
                parts=tuple(_candidate_rule(candidate) for candidate in members) if len(members) > 1 else (),
                origin="grouped" if len(members) > 1 else members[0].kind,
            )
        )
    return rules


def _candidate_rule(candidate: Candidate) -> ComponentRule:
    return ComponentRule(
        component_id="",
        name=candidate_name(candidate),
        prefixes=candidate.prefixes,
        terms=candidate.terms,
        fallback_prefixes=candidate.fallback_prefixes,
        origin=candidate.kind,
    )


def _settle(
    scope_id: ScopeId,
    units: list[Unit],
    rules: list[ComponentRule],
    role_words: frozenset[str],
    rung: str,
    *,
    guard: bool,
    min_rules: int = 2,
) -> tuple[ScopeSpec, Partition] | None:
    """Replay the rules, apply the guard, number the survivors, and bucket what is left.

    The guard: at least ``min_rules`` rules with a prefix or a word must each hold
    ``max(MIN_UNITS, int(GUARD_SHARE * parent))`` units, else the rung does not count. A
    smaller rule the grouper found no sibling for stays its own small box: the names drew
    it, and folding it into the largest rule was measured to make a grab bag of that rule.
    A fallback-only rule (loose files, a layer's residue) is not counted: it is the scope's
    last resort and stays its own box however small.
    """
    if len(rules) < min_rules:
        return None
    provisional = [replace(rule, component_id=f"?{index}") for index, rule in enumerate(rules)]
    partition = replay(units, ScopeSpec(scope_id, provisional, rung=rung), role_words)
    sizes = {rule.component_id: partition.size(rule.component_id) for rule in provisional}
    floor = _floor(len(units)) if guard else 1
    strong = [rule for rule in provisional if not rule.is_fallback_only and sizes[rule.component_id] >= floor]
    if len(strong) < min_rules:
        return None
    kept = [rule for rule in provisional if sizes[rule.component_id] or rule.is_fallback_only]
    ordered = sorted(kept, key=lambda rule: (-sizes[rule.component_id], rule.name))
    scope = ScopeSpec(scope_id, rung=rung)
    for rule in ordered:
        scope.rules.append(replace(rule, component_id=scope.next_id()))
    partition = replay(units, scope, role_words)
    if partition.unplaced:
        scope.rules.append(ComponentRule(scope.next_id(), UNPLACED_NAME, origin=UNPLACED, kind=UNPLACED))
        partition = replay(units, scope, role_words)
    return scope, partition


def _floor(unit_count: int) -> int:
    return max(MIN_UNITS, int(GUARD_SHARE * unit_count))


def _dedupe(terms: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(terms))
