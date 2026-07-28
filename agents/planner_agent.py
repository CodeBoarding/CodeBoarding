"""Deterministic component expansion planning — which components split further.

Two independent gates, no LLM:

* ``should_expand_component`` — structural. A component needs files, and needs
  either its own leaf clusters or a parent that had them (one file-level level).
* ``component_is_separable`` — size and cohesion. Past the leaf ceiling a
  component splits whatever its call structure says; below it, the split's
  modularity must clear a bar that eases as the component approaches the ceiling.

See docs/development/component-sizing.md for the constants and the measurements
behind them.
"""

import logging
from collections.abc import Callable

import networkx as nx

from agents.agent_responses import AnalysisInsights, Component
from static_analyzer.cluster_helpers import SUBCOMPONENTS_MAX, SUBCOMPONENTS_MIN, supercluster_leaf_ids
from static_analyzer.graph import METHOD_LEVEL_STRATEGY, ClusterResult

logger = logging.getLogger(__name__)

# Default thresholds
DEFAULT_MIN_FILES = 1  # Need at least 1 file to have content

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
        logger.debug(f"[Planner] subgraph too small to expand ({total_methods} < {min_methods} methods)")
        return False
    if all(cr.strategy == METHOD_LEVEL_STRATEGY for cr in cluster_results.values()):
        # One synthetic cluster per method: the meta-graph is the raw call graph, whose
        # modularity is far higher than any real clustering's and not comparable to the
        # threshold. Too few natural clusters to separate means there is nothing to split.
        logger.debug("[Planner] subgraph has no natural cluster structure; keeping as leaf")
        return False
    _groups, modularity = supercluster_leaf_ids(cluster_results, cfg_graphs, SUBCOMPONENTS_MIN, SUBCOMPONENTS_MAX)
    required = EXPAND_MODULARITY_THRESHOLD * max(0.0, 1.0 - load)
    separable = modularity >= required
    logger.debug(
        f"[Planner] subgraph modularity={modularity:.4f} (load={load:.2f}, required {required:.4f}) "
        f"-> separable={separable}"
    )
    return separable


def should_expand_component(
    component: Component,
    parent_had_clusters: bool = True,
    min_files: int = DEFAULT_MIN_FILES,
) -> bool:
    """
    Determine if a component should be expanded into sub-components.

    Expansion logic:
    - If component has clusters -> expand (there's CFG structure to decompose)
    - If component has no clusters but has files -> expand if parent had clusters
      (allows one more level to explain file internals)
    - If neither component nor parent has clusters -> stop (leaf node)

    Note: Method-level cluster expansion is handled separately in
    cluster_methods_mixin._expand_to_method_level_clusters() when a subgraph
    has fewer than MIN_CLUSTERS_THRESHOLD clusters. This ensures the planner
    doesn't need to worry about method counts.

    Args:
        component: The component to evaluate
        parent_had_clusters: Whether the parent component had source_cluster_ids.
                            True for top-level components (from AbstractionAgent).
        min_files: Minimum number of assigned files required (default: 1)

    Returns:
        True if the component should be expanded, False otherwise
    """
    has_clusters = bool(component.source_cluster_ids)
    has_files = len(component.file_methods) >= min_files

    # A component without files has nothing to expand regardless of clusters
    if not has_files:
        logger.debug(
            f"Component '{component.name}' has no file_methods ({len(component.file_methods)} < {min_files}), "
            f"skipping expansion"
        )
        return False

    # If component has clusters, it's expandable (CFG structure exists)
    if has_clusters:
        logger.debug(
            f"Component '{component.name}' is expandable: "
            f"{len(component.source_cluster_ids)} clusters, {len(component.file_methods)} files"
        )
        return True

    # Component has no clusters but has files
    # Only expand if parent had clusters (allow one more level)
    if parent_had_clusters:
        logger.debug(
            f"Component '{component.name}' is expandable (file-level): "
            f"no clusters but {len(component.file_methods)} files, parent had clusters"
        )
        return True

    # Neither parent nor current has clusters - we're at leaf level
    logger.debug(f"Component '{component.name}' is a leaf: no clusters and parent also had no clusters")
    return False


def get_expandable_components(
    analysis: AnalysisInsights,
    parent_had_clusters: bool = True,
    min_files: int = DEFAULT_MIN_FILES,
    separable: Callable[[Component], bool] | None = None,
) -> list[Component]:
    """
    Determine which components should be expanded for deeper analysis.

    This is a deterministic operation based on CFG structure - no LLM calls.

    Args:
        analysis: The analysis containing components to evaluate
        parent_had_clusters: Whether the parent component (that produced this analysis)
                            had source_cluster_ids. True for top-level analysis.
        min_files: Minimum files for expansion (default: 1)
        separable: Optional predicate that also requires a component's own call
            structure to actually split (see ``component_is_separable``). When given,
            cohesive components are kept as leaves instead of being force-expanded to
            the depth cap. Omitted (None) preserves the structural-only behaviour.

    Returns:
        List of components that should be expanded
    """
    expandable: list[Component] = []
    for component in analysis.components:
        if not should_expand_component(component, parent_had_clusters, min_files):
            continue
        if separable is not None and not separable(component):
            logger.info(f"[Planner] Component '{component.name}' is cohesive (below split threshold); keeping as leaf")
            continue
        expandable.append(component)

    logger.info(f"[Planner] {len(expandable)}/{len(analysis.components)} components eligible for expansion")

    return expandable
