"""The ladder: a scope's rules drafted one rung at a time, the grouper its one judgement."""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import Protocol

from clustering_ids import ROOT_SCOPE_ID, ScopeId
from static_analyzer.clustering.names.frontier import BOX, FILE, HEAD, LOOSE, RESIDUAL, SHARE, WORD, Candidate, walk
from static_analyzer.clustering.names.inventory import Trie, Unit
from static_analyzer.clustering.names.replay import Partition, replay
from static_analyzer.clustering.names.spec import (
    FILES,
    FRONTIER,
    LAYERS,
    LEAF,
    ROLE,
    SEGMENT,
    UNMERGE,
    UNPLACED,
    VOCABULARY,
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
"""Units above which a component transposes a layered sub-tree and reads its words."""
BUDGET = 9
"""Components a rung is folded toward; the guard, not the budget, is what a rung must clear."""
MIN_LINKS = 2
"""Graph links two candidates must exchange before they count as affine: one is noise."""
CAP_SHARE = 0.6
"""No fold may grow a component past this share of its scope."""
UNPLACED_NAME = "Unassigned"
LOOSE_NAME = "Loose files"

Links = Mapping[tuple[str, str], int]
"""Weight of the graph edges between two units, keyed by the unit ids in sorted order."""


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
    """Kinship, then fold the rung along the graph: a candidate below the floor, and the
    smallest candidate while the rung is over budget, joins the sibling it exchanges the most
    links with against what their sizes predict.

    Why observed over expected: a hub (``utils``, ``core``) talks to everyone, so raw counts
    would fold every small box into it; against its degree it is nobody's closest sibling.
    """

    name = "affinity"

    def group(self, candidates: Sequence[Candidate], context: GroupingContext) -> list[CandidateGroup]:
        groups = KinshipGrouper().group(candidates, context)
        members = [list(group.keys) for group in groups]
        terms = [group.terms for group in groups]
        sizes = [sum(context.sizes.get(key, 0) for key in group.keys) for group in groups]
        links = [
            [sum(context.links.get((min(a, b), max(a, b)), 0) for a in left for b in right) for right in members]
            for left in members
        ]
        for index in range(len(members)):
            links[index][index] = 0
        while True:
            live = sorted((index for index in range(len(members)) if sizes[index]), key=lambda i: (sizes[i], i))
            sources = [index for index in live if sizes[index] < context.floor]
            if len(live) > BUDGET:
                sources = live
            chosen = self._fold(sources, live, sizes, links, context)
            if chosen is None:
                break
            source, target = chosen
            members[target].extend(members[source])
            terms[target] = _dedupe(terms[target] + terms[source])
            sizes[target] += sizes[source]
            sizes[source] = 0
            for other in range(len(members)):
                links[target][other] += links[source][other]
                links[other][target] += links[other][source]
                links[source][other] = links[other][source] = 0
            links[target][target] = 0
            members[source] = []
        by_key = {candidate.key: candidate for candidate in candidates}
        groups = []
        for index, keys in enumerate(members):
            if not keys:
                continue
            # The biggest member names a fold: the box a reader already recognises.
            biggest = max(context.sizes.get(key, 0) for key in keys)
            name = _plainest([by_key[key] for key in keys if context.sizes.get(key, 0) == biggest])
            groups.append(CandidateGroup(name, tuple(keys), terms[index]))
        return groups

    @staticmethod
    def _fold(
        sources: list[int], live: list[int], sizes: list[int], links: list[list[int]], context: GroupingContext
    ) -> tuple[int, int] | None:
        """The smallest source with an affine sibling, and that sibling: ``(source, target)``."""
        degree = [sum(row) for row in links]
        total = sum(degree) / 2 or 1.0
        cap = CAP_SHARE * context.unit_count
        for source in sources:
            best: tuple[float, int, int] | None = None
            for target in live:
                count = links[source][target]
                if target == source or count < MIN_LINKS or sizes[source] + sizes[target] > cap:
                    continue
                affinity = count * total / (degree[source] * degree[target])
                if best is None or (affinity, -sizes[target], -target) > best:
                    best = (affinity, -sizes[target], -target)
            if best is not None:
                return source, -best[2]
        return None


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
    share: float = SHARE,
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
        scope, partition = draft_scope(
            scope_id, scope_units, role_words, grouper, parts=parts, share=share, links=links
        )
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
    share: float = SHARE,
    links: Links | None = None,
) -> tuple[ScopeSpec, Partition]:
    """Draft one scope's rules from its units: the first rung that splits it wins."""
    scope_units = list(units)
    links = links or {}
    rungs: list[tuple[str, Callable[[], tuple[list[ComponentRule], str]]]] = []

    def frontier(rung: str, transpose: bool, layers: bool = False) -> tuple[list[ComponentRule], str]:
        return _frontier_rules(scope_id, scope_units, role_words, grouper, share, rung, links, transpose, layers)

    def vocabulary() -> tuple[list[ComponentRule], str]:
        return _vocabulary_rules(scope_id, scope_units, role_words, grouper, links)

    if len(parts) >= 2:
        rungs.append((UNMERGE, lambda: (list(parts), "structural")))
    if is_root(scope_id):
        rungs.append((FRONTIER, lambda: frontier(FRONTIER, True)))
        rungs.append((VOCABULARY, vocabulary))
    elif len(scope_units) > LEAF_UNITS:
        transpose = len(scope_units) > LEAF_CAP
        rungs.append((SEGMENT, lambda: frontier(SEGMENT, transpose)))
        if transpose:
            rungs.append((VOCABULARY, vocabulary))
        rungs.append((LAYERS, lambda: frontier(LAYERS, False, layers=True)))
        rungs.append((FILES, lambda: _file_rules(scope_id, scope_units, role_words, grouper, links)))
        rungs.append((ROLE, lambda: _role_rules(scope_id, scope_units, role_words, grouper, links)))
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
        # Never refuse: a root nothing splits is drawn as the one box the frontier gave it.
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


def _frontier_rules(
    scope_id: ScopeId,
    units: list[Unit],
    role_words: frozenset[str],
    grouper: Grouper,
    share: float,
    rung: str,
    links: Links,
    transpose: bool,
    layers: bool = False,
) -> tuple[list[ComponentRule], str]:
    frontier = walk(Trie(units), role_words, share=share, transpose=transpose, layers=layers)
    candidates = sorted(frontier.candidates, key=lambda candidate: candidate.key)
    if not candidates:
        return [], frontier.axis
    context = _context(scope_id, units, candidates, role_words, rung, links)
    return _rules_from_groups(grouper.group(candidates, context), candidates), frontier.axis


def _vocabulary_rules(
    scope_id: ScopeId,
    units: list[Unit],
    role_words: frozenset[str],
    grouper: Grouper,
    links: Links,
) -> tuple[list[ComponentRule], str]:
    """One candidate per word the units' own names elect, head noun weighing most."""
    counts: Counter[str] = Counter()
    for unit in units:
        counts.update(_unit_stems(unit))
    ubiquitous = frozenset(word for word, count in counts.items() if count >= 2 and count >= len(units) / 2)
    keys = sorted({key for unit in units if (key := _vocabulary_key(unit, role_words, ubiquitous))})
    candidates = [Candidate(f"{WORD}:{key}", WORD, key, terms=(key,)) for key in keys]
    if len(candidates) < 2:
        return [], VOCABULARY
    context = _context(scope_id, units, candidates, role_words, VOCABULARY, links)
    return _rules_from_groups(grouper.group(candidates, context), candidates), VOCABULARY


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
    """Size, a few identifiers and links per candidate, from a replay of the candidates as rules."""
    provisional = ScopeSpec(
        scope_id,
        [replace(_candidate_rule(candidate), component_id=candidate.key) for candidate in candidates],
        rung=rung,
    )
    partition = replay(units, provisional, role_words)
    between: Counter[tuple[str, str]] = Counter()
    for (left, right), weight in links.items():
        left_owner, right_owner = partition.assignment.get(left), partition.assignment.get(right)
        if left_owner and right_owner and left_owner != right_owner:
            between[(min(left_owner, right_owner), max(left_owner, right_owner))] += weight
    samples: dict[str, tuple[str, ...]] = {}
    for candidate in candidates:
        seen: dict[str, None] = {}
        for unit in partition.members.get(candidate.key, []):
            for name in unit.names:
                seen.setdefault(segments(name, ClusteringConfig.QUALIFIED_NAME_DELIMITER)[-1], None)
                if len(seen) >= SAMPLE_IDENTIFIERS:
                    break
            if len(seen) >= SAMPLE_IDENTIFIERS:
                break
        samples[candidate.key] = tuple(seen)
    sizes = {candidate.key: partition.size(candidate.key) for candidate in candidates}
    floor = MIN_UNITS if rung == FRONTIER else _floor(len(units))
    return GroupingContext(scope_id, role_words, len(units), rung, sizes, samples, dict(between), floor)


def _unit_stems(unit: Unit) -> set[str]:
    return {
        stem(word)
        for name in unit.names
        for part in segments(name, ClusteringConfig.QUALIFIED_NAME_DELIMITER)
        for word in tokenize(part)
    }


def _vocabulary_key(unit: Unit, role_words: frozenset[str], ubiquitous: frozenset[str]) -> str:
    votes: Counter[str] = Counter()
    for name in unit.names:
        for part in segments(name, ClusteringConfig.QUALIFIED_NAME_DELIMITER):
            words = tokenize(part)
            for position, word in enumerate(words):
                key = stem(word)
                if key not in role_words and key not in ubiquitous:
                    votes[key] += 2.0 ** (position - (len(words) - 1))
    return max(sorted(votes), key=lambda key: (votes[key], key)) if votes else ""


def _plainest(members: list[Candidate]) -> str:
    """``Ordering`` over ``OrderProcessor``: the member with the fewest words names the group."""
    return candidate_name(min(members, key=lambda candidate: (len(tokenize(candidate.label)), candidate.label)))


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
