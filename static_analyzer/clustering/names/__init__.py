"""Partition a repository by what its qualified names say: one tree, replayed everywhere.

``inventory`` reads units (files) and their positions off the call graph's names; ``frontier``
walks the trie of those positions and emits candidate rules; ``draft`` groups candidates into
components rung by rung and writes the result as a ``spec``; ``replay`` is the one pure
function from units and a scope's rules to a partition, used at draft time and on every
incremental and partial run after it. See ``docs/design/name-tree.md``.
"""

from static_analyzer.clustering.names.draft import (
    CandidateGroup,
    Grouper,
    GroupingContext,
    KinshipGrouper,
    draft_scope,
    draft_tree,
    role_words_for,
)
from static_analyzer.clustering.names.frontier import Candidate, Frontier, walk
from static_analyzer.clustering.names.inventory import Trie, Unit, unit_position, units_from_graph, units_from_graphs
from static_analyzer.clustering.names.replay import Partition, replay
from static_analyzer.clustering.names.spec import ComponentRule, ScopeSpec, TreeSpec
from static_analyzer.clustering.names.tokens import LAYOUT_WORDS, ROLE_WORDS, stem, tokenize

__all__ = [
    "Candidate",
    "CandidateGroup",
    "ComponentRule",
    "Frontier",
    "Grouper",
    "GroupingContext",
    "KinshipGrouper",
    "LAYOUT_WORDS",
    "Partition",
    "ROLE_WORDS",
    "ScopeSpec",
    "TreeSpec",
    "Trie",
    "Unit",
    "draft_scope",
    "draft_tree",
    "replay",
    "role_words_for",
    "stem",
    "tokenize",
    "unit_position",
    "units_from_graph",
    "units_from_graphs",
    "walk",
]
