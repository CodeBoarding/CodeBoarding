"""Jaccard-based gate for the name-stability arbiter.

Given the prior analysis run's clusters (each with a name + member set) and
a new cluster's member set, find the prior cluster that's most similar by
Jaccard. Used as a deterministic gate before invoking the LLM arbiter:
above threshold = "same cluster, decide NOOP/UPDATE"; below = "treat as
new, fall through to full naming."
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class PriorCluster:
    """One cluster from the prior analysis run, with its name and members."""

    name: str
    members: frozenset[str]


class PriorClusterIndex:
    """Best-match lookup over prior clusters by Jaccard similarity.

    Why frozen + frozenset: the prior set is read-only across an analysis
    run, and frozensets give us O(min(|a|,|b|)) intersection without copies.
    """

    def __init__(self, priors: list[PriorCluster]):
        self._priors = priors

    @classmethod
    def from_pairs(cls, pairs: list[tuple[str, list[str]]]) -> "PriorClusterIndex":
        """Convenience constructor: ``[("Auth", ["m1","m2"]), ...]``."""
        return cls([PriorCluster(name=n, members=frozenset(m)) for n, m in pairs])

    def find_best_match(
        self,
        new_members: list[str] | set[str],
        threshold: float = 0.5,
    ) -> tuple[PriorCluster, float] | None:
        """Return the prior with highest Jaccard ≥ ``threshold``, or None.

        Why None on no match: callers (e.g. the arbiter dispatch path) need
        to distinguish "near-miss → arbitrate" from "genuinely new → re-derive."
        Returning ``(prior, score)`` instead of just the prior gives observability
        for telemetry without forcing callers to re-compute the score.

        Threshold default 0.5: at least half the union must overlap. Matches
        the typical refactoring-classifier "same component" floor and is the
        empirical sweet spot in the literature for "this is meaningfully the
        same cluster."

        Ties broken by larger prior set (more evidence for the match), then
        lexicographically by name (deterministic).
        """
        new_set = frozenset(new_members)
        if not new_set:
            return None

        best: tuple[PriorCluster, float] | None = None
        best_tiebreak: tuple[int, str] = (-1, "")

        for prior in self._priors:
            if not prior.members:
                continue
            score = _jaccard(prior.members, new_set)
            if score < threshold:
                continue
            tiebreak = (len(prior.members), prior.name)
            if best is None or score > best[1] or (score == best[1] and tiebreak > best_tiebreak):
                best = (prior, score)
                best_tiebreak = tiebreak

        return best


def _jaccard(a: frozenset[str], b: frozenset[str]) -> float:
    """Jaccard similarity. Empty union returns 0.0 (not 1.0) — two empty
    sets aren't "the same cluster," they're both undefined."""
    union = a | b
    if not union:
        return 0.0
    return len(a & b) / len(union)
