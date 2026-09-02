"""The ladder: how a scope's rules are drafted one rung at a time, and where it stops.

The root reads the frontier of its trie, then its units' words if that gave one box. A
component splits back into the candidates that were grouped into it; only a component above
``LEAF_CAP`` goes on to the frontier of its own sub-trie and then to its units' words. A rung
counts when at least two children each clear the guard; a scope no rung can split is a leaf
that says why. Every rung is candidates in, rules out: ``replay`` places the units, at draft
time as on every later run.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field, replace
from typing import Protocol

from clustering_ids import ROOT_SCOPE_ID, ScopeId
from static_analyzer.clustering.names.frontier import BOX, LOOSE, RESIDUAL, SHARE, WORD, Candidate, walk
from static_analyzer.clustering.names.inventory import Trie, Unit
from static_analyzer.clustering.names.replay import Partition, replay
from static_analyzer.clustering.names.spec import (
    UNPLACED,
    ComponentRule,
    ScopeSpec,
    TreeSpec,
    is_root,
)
from static_analyzer.clustering.names.tokens import (
    ROLE_WORDS,
    distinctive_word,
    segments,
    stem,
    tokenize,
    ubiquitous_words,
)
from static_analyzer.config import ClusteringConfig

MIN_UNITS = 2
GUARD_SHARE = 0.05
LEAF_CAP = 135
"""Units a component may hold and still be a leaf without reading its sub-tree or its words."""
UNPLACED_NAME = "Unassigned"

UNMERGE = "unmerge"
FRONTIER = "frontier"
VOCABULARY = "vocabulary"
SEGMENT = "segment"
LEAF = "leaf"


@dataclass(frozen=True)
class GroupingContext:
    """What a grouper may know beyond the candidates: the scope, and per candidate its size
    and a few identifiers, so a planner can judge without ever seeing a unit."""

    scope_id: ScopeId
    role_words: frozenset[str]
    unit_count: int
    rung: str
    sizes: dict[str, int] = field(default_factory=dict)
    samples: dict[str, tuple[str, ...]] = field(default_factory=dict)


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


def candidate_name(candidate: Candidate) -> str:
    if candidate.kind == LOOSE:
        where = ".".join(candidate.fallback_prefixes[0]) if candidate.fallback_prefixes else ""
        return f"Loose files in {where}" if where else "Loose files"
    if candidate.kind == RESIDUAL:
        return f"{candidate.label} (residual)"
    if candidate.kind == WORD:
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
) -> TreeSpec:
    """Draft the root and every scope below it down to ``max_depth``."""
    if max_depth < 1:
        raise ValueError("max_depth must be at least 1")
    machinery = frozenset(machinery)
    spec = TreeSpec(machinery=machinery, grouper=grouper.name)
    role_words = role_words_for(machinery)

    def build(scope_id: ScopeId, scope_units: list[Unit], parts: tuple[ComponentRule, ...], depth: int) -> None:
        scope, partition = draft_scope(scope_id, scope_units, role_words, grouper, parts=parts, share=share)
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
) -> tuple[ScopeSpec, Partition]:
    """Draft one scope's rules from its units: the first rung that splits it wins."""
    scope_units = list(units)
    rungs: list[tuple[str, Callable[[], tuple[list[ComponentRule], str]]]] = []
    if len(parts) >= 2:
        rungs.append((UNMERGE, lambda: (list(parts), "structural")))
    if is_root(scope_id):
        rungs.append((FRONTIER, lambda: _frontier_rules(scope_id, scope_units, role_words, grouper, share, FRONTIER)))
        rungs.append((VOCABULARY, lambda: _vocabulary_rules(scope_id, scope_units, role_words, grouper)))
    elif len(scope_units) > LEAF_CAP:
        rungs.append((SEGMENT, lambda: _frontier_rules(scope_id, scope_units, role_words, grouper, share, SEGMENT)))
        rungs.append((VOCABULARY, lambda: _vocabulary_rules(scope_id, scope_units, role_words, grouper)))
    produced: dict[str, tuple[list[ComponentRule], str]] = {}
    for rung, produce in rungs:
        produced[rung] = rules, axis = produce()
        settled = _settle(scope_id, scope_units, rules, role_words, guard=rung != FRONTIER)
        if settled is None:
            continue
        scope, partition = settled
        scope.axis = axis
        scope.rung = rung
        return scope, partition
    if is_root(scope_id) and scope_units:
        # Never refuse: a root nothing splits is drawn as the one box the frontier gave it.
        rules, axis = produced[FRONTIER]
        settled = _settle(scope_id, scope_units, rules, role_words, guard=False, min_rules=1)
        if settled is not None:
            scope, partition = settled
            scope.axis = axis
            scope.rung = FRONTIER
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
    if unit_count <= LEAF_CAP:
        return f"cohesive: {unit_count} units, at most {LEAF_CAP}; {unmerge}"
    return f"exhausted: {unmerge}; neither the sub-tree nor the words yield two children of {unit_count} units"


def _frontier_rules(
    scope_id: ScopeId,
    units: list[Unit],
    role_words: frozenset[str],
    grouper: Grouper,
    share: float,
    rung: str,
) -> tuple[list[ComponentRule], str]:
    frontier = walk(Trie(units), role_words, share=share)
    candidates = sorted(frontier.candidates, key=lambda candidate: candidate.key)
    if not candidates:
        return [], frontier.axis
    context = _context(scope_id, units, candidates, role_words, rung)
    return _rules_from_groups(grouper.group(candidates, context), candidates), frontier.axis


def _vocabulary_rules(
    scope_id: ScopeId,
    units: list[Unit],
    role_words: frozenset[str],
    grouper: Grouper,
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
    context = _context(scope_id, units, candidates, role_words, VOCABULARY)
    return _rules_from_groups(grouper.group(candidates, context), candidates), VOCABULARY


SAMPLE_IDENTIFIERS = 6


def _context(
    scope_id: ScopeId,
    units: list[Unit],
    candidates: Sequence[Candidate],
    role_words: frozenset[str],
    rung: str,
) -> GroupingContext:
    """Size and a few identifiers per candidate, from a replay of the candidates as rules."""
    provisional = ScopeSpec(
        scope_id, [replace(_candidate_rule(candidate), component_id=candidate.key) for candidate in candidates]
    )
    partition = replay(units, provisional, role_words)
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
    return GroupingContext(scope_id, role_words, len(units), rung, sizes, samples)


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
    if missing or unknown or repeated:
        raise ValueError(
            f"grouping must cover every candidate once: missing={missing} unknown={unknown} repeated={repeated}"
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
    *,
    guard: bool,
    min_rules: int = 2,
) -> tuple[ScopeSpec, Partition] | None:
    """Replay the rules, apply the guard, number the survivors, and bucket what is left.

    The guard: at least ``min_rules`` rules with a prefix or a word must each hold
    ``max(MIN_UNITS, int(GUARD_SHARE * parent))`` units; a smaller one is absorbed by the
    largest. A fallback-only rule (loose files, a layer's residue) is neither counted nor
    absorbed: it is the scope's last resort and stays its own box however small.
    """
    if len(rules) < min_rules:
        return None
    provisional = [replace(rule, component_id=f"?{index}") for index, rule in enumerate(rules)]
    partition = replay(units, ScopeSpec(scope_id, provisional), role_words)
    sizes = {rule.component_id: partition.size(rule.component_id) for rule in provisional}
    floor = _floor(len(units)) if guard else 1
    claimants = [rule for rule in provisional if not rule.is_fallback_only]
    strong = [rule for rule in claimants if sizes[rule.component_id] >= floor]
    if len(strong) < min_rules:
        return None
    kept = {rule.component_id: rule for rule in provisional if rule in strong or rule.is_fallback_only}
    largest = max(strong, key=lambda rule: sizes[rule.component_id]).component_id
    for weak in (rule for rule in claimants if rule not in strong and sizes[rule.component_id]):
        kept[largest] = _merge_rules(kept[largest], weak)
        sizes[largest] += sizes[weak.component_id]
    ordered = sorted(kept.values(), key=lambda rule: (-sizes[rule.component_id], rule.name))
    scope = ScopeSpec(scope_id)
    for rule in ordered:
        scope.rules.append(replace(rule, component_id=scope.next_id()))
    partition = replay(units, scope, role_words)
    if partition.unplaced:
        scope.rules.append(ComponentRule(scope.next_id(), UNPLACED_NAME, origin=UNPLACED, kind=UNPLACED))
        partition = replay(units, scope, role_words)
    return scope, partition


def _floor(unit_count: int) -> int:
    return max(MIN_UNITS, int(GUARD_SHARE * unit_count))


def _merge_rules(target: ComponentRule, weak: ComponentRule) -> ComponentRule:
    return replace(
        target,
        prefixes=target.prefixes + weak.prefixes,
        terms=_dedupe(target.terms + weak.terms),
        fallback_prefixes=target.fallback_prefixes + weak.fallback_prefixes,
        parts=(target.parts or (_as_part(target),)) + (weak.parts or (_as_part(weak),)),
    )


def _as_part(rule: ComponentRule) -> ComponentRule:
    return replace(rule, component_id="", parts=())


def _dedupe(terms: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(terms))
