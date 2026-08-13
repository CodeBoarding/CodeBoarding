"""Deterministic component expansion planning — which components split further.

``should_expand_component`` is the structural gate, no LLM: a component needs
files, and needs either its own leaf clusters or a parent that had them (one
file-level level). The size/cohesion gate lives in
``static_analyzer.clustering.separability`` and is applied by
``ClusteringService.component_is_separable``.
"""

import logging
from collections.abc import Callable

from agents.agent_responses import AnalysisInsights, Component

logger = logging.getLogger(__name__)

# Default thresholds
DEFAULT_MIN_FILES = 1  # Need at least 1 file to have content


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
    clustering.service (``ClusteringService.cluster_component``) when a subgraph
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
