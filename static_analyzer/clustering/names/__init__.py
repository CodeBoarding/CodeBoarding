"""Partition a repository by what its qualified names say: one tree, replayed everywhere.

See ``docs/design/name-tree.md``.
"""

from static_analyzer.clustering.names.draft import (
    AffinityGrouper,
    CandidateGroup,
    Grouper,
    GroupingContext,
    KinshipGrouper,
    draft_scope,
    draft_tree,
    role_words_for,
)
from static_analyzer.clustering.names.frontier import Candidate, Frontier, walk
from static_analyzer.clustering.names.inventory import (
    Trie,
    Unit,
    units_from_graph,
    units_from_graphs,
)
from static_analyzer.clustering.names.replay import Partition, replay
from static_analyzer.clustering.names.spec import ComponentRule, ScopeSpec, TreeSpec
from static_analyzer.clustering.names.tokens import ROLE_WORDS, stem, tokenize

__all__ = [
    "ROLE_WORDS",
    "AffinityGrouper",
    "Candidate",
    "CandidateGroup",
    "ComponentRule",
    "Frontier",
    "Grouper",
    "GroupingContext",
    "KinshipGrouper",
    "Partition",
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
    "units_from_graph",
    "units_from_graphs",
    "walk",
]
