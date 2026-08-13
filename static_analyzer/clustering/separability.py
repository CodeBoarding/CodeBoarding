"""Deterministic split-or-keep policy: is a component's call structure worth sub-dividing.

See docs/development/component-sizing.md for the constants and the measurements
behind them.
"""

import logging

import networkx as nx

from agents.agent_responses import Component
from static_analyzer.clustering.cluster_helpers import SUBCOMPONENTS_MAX, SUBCOMPONENTS_MIN, supercluster_leaf_ids
from static_analyzer.clustering.models import METHOD_LEVEL_STRATEGY, ClusterResult

logger = logging.getLogger(__name__)

# Below this a component holds too little to be worth sub-dividing at all.
MIN_METHODS_TO_EXPAND = 30

# The size at which a component stops being readable as one box and must be split
# whatever its call structure says. Measured across the eval corpus: at 12 files /
# 120 methods every repo's tree comes out with no oversized leaf, while small repos
# are untouched (they stop at the modularity gate long before this).
MAX_LEAF_FILES = 12
MAX_LEAF_METHODS = 120

# Modularity a *small* component's split must reach to be worth making. The bar
# ramps linearly to zero as the component approaches the leaf ceiling: a large
# component gets split on weaker structural evidence, because leaving it whole
# costs the reader more than an imperfect boundary does.
EXPAND_MODULARITY_THRESHOLD = 0.15


def member_keys(component: Component) -> frozenset[tuple[str, str]]:
    """The ``(file_path, qualified_name)`` set a component owns — its membership identity."""
    return frozenset(
        (group.file_path, method.qualified_name) for group in component.file_methods for method in group.methods
    )


def leaf_load(component: Component) -> float:
    """How full a component is against the leaf ceiling; >= 1.0 means too big to leave whole."""
    methods = sum(len(group.methods) for group in component.file_methods)
    return max(methods / MAX_LEAF_METHODS, len(component.file_methods) / MAX_LEAF_FILES)


def component_is_separable(
    cluster_results: dict[str, ClusterResult],
    cfg_graphs: dict[str, nx.DiGraph],
    load: float,
    min_methods: int = MIN_METHODS_TO_EXPAND,
) -> bool:
    """Whether a component's own call structure justifies splitting it into sub-components.

    Requires enough content (>= ``min_methods`` methods) and a split whose modularity
    clears the size-graded bar. ``load`` is the caller's ``leaf_load``; callers that
    already know the component is oversized (``load >= 1.0``) should skip this and
    split unconditionally rather than pay for the partition sweep.
    """
    total_methods = sum(len(members) for cr in cluster_results.values() for members in cr.clusters.values())
    if total_methods < min_methods:
        logger.debug(f"[Separability] subgraph too small to expand ({total_methods} < {min_methods} methods)")
        return False
    if all(cr.strategy == METHOD_LEVEL_STRATEGY for cr in cluster_results.values()):
        # One synthetic cluster per method: the meta-graph is the raw call graph, whose
        # modularity is far higher than any real clustering's and not comparable to the
        # threshold. Too few natural clusters to separate means there is nothing to split.
        logger.debug("[Separability] subgraph has no natural cluster structure; keeping as leaf")
        return False
    _groups, modularity = supercluster_leaf_ids(cluster_results, cfg_graphs, SUBCOMPONENTS_MIN, SUBCOMPONENTS_MAX)
    required = EXPAND_MODULARITY_THRESHOLD * max(0.0, 1.0 - load)
    separable = modularity >= required
    logger.debug(
        f"[Separability] subgraph modularity={modularity:.4f} (load={load:.2f}, required {required:.4f}) "
        f"-> separable={separable}"
    )
    return separable
