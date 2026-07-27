"""
Deterministic component expansion planning.

This module provides fast, deterministic logic to decide which components
should be expanded into sub-components. Unlike the previous LLM-based approach,
this uses CFG clustering structure as the source of truth.

Expansion Rules:
1. If component has source_cluster_ids -> expandable (CFG structure exists)
2. If component has no clusters but has files -> expandable ONE level (to explain files)
3. If neither component nor its parent has clusters -> leaf (stop expanding)

Note: The MIN_CLUSTERS_THRESHOLD in constants.py controls when subgraphs are
automatically expanded to method-level clustering in cluster_methods_mixin.py.
This ensures fine-grained method assignment even for small components.

Example:
- Component: "Agents" (clusters: [1,2,3]) -> expand (yes)
  - Sub-component: "DetailsAgent" (clusters: [], files: [details_agent.py]) -> expand (yes, parent had clusters)
    - Sub-sub-component: "run_method" (clusters: [], files: []) -> DON'T expand (no, parent had no clusters)
"""

import logging
from collections.abc import Callable

import networkx as nx

from agents.agent_responses import AnalysisInsights, Component
from static_analyzer.cluster_helpers import subgraph_peak_modularity
from static_analyzer.graph import ClusterResult

logger = logging.getLogger(__name__)

# Default thresholds
DEFAULT_MIN_FILES = 1  # Need at least 1 file to have content

# A component is only expanded into sub-components when its own call structure
# actually separates. Both gates must pass:
#   - MIN_METHODS_TO_EXPAND: below this a component is too small to sub-divide.
#   - EXPAND_MODULARITY_THRESHOLD: peak modularity of the component's inter-cluster
#     meta-graph; below it the internals are a cohesive blob with no natural split.
# Why: modularity is a continuous ramp with no bimodal split, so the threshold is a
# depth dial — 0.20 is the deepest setting where every split still maps to genuine
# structure; raise toward 0.25 for shallower trees.
MIN_METHODS_TO_EXPAND = 30
EXPAND_MODULARITY_THRESHOLD = 0.15


def component_is_separable(
    cluster_results: dict[str, ClusterResult],
    cfg_graphs: dict[str, nx.DiGraph],
    min_modularity: float = EXPAND_MODULARITY_THRESHOLD,
    min_methods: int = MIN_METHODS_TO_EXPAND,
) -> bool:
    """Whether a component's own call structure justifies splitting it into sub-components.

    True only when the subgraph is big enough (>= ``min_methods`` methods) AND its
    inter-cluster meta-graph has a genuine community split (peak modularity
    >= ``min_modularity``). A cohesive blob (low modularity) or a small component
    becomes a leaf instead of being force-split down to the depth cap.
    """
    total_methods = sum(len(members) for cr in cluster_results.values() for members in cr.clusters.values())
    if total_methods < min_methods:
        logger.debug(f"[Planner] subgraph too small to expand ({total_methods} < {min_methods} methods)")
        return False
    modularity = subgraph_peak_modularity(cluster_results, cfg_graphs)
    separable = modularity >= min_modularity
    logger.debug(
        f"[Planner] subgraph peak modularity={modularity:.4f} " f"(threshold {min_modularity}) -> separable={separable}"
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
