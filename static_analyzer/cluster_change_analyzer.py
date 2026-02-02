"""
Cluster change analysis for incremental static analysis.

This module provides functionality to compare cluster results between two analysis runs,
match clusters based on node overlap, and classify the magnitude of structural changes.
"""

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Set, Tuple

from static_analyzer.graph import ClusterResult

logger = logging.getLogger(__name__)


class ChangeClassification(Enum):
    """Classification of cluster change magnitude."""

    SMALL = "small"  # Minor changes within existing clusters
    MEDIUM = "medium"  # Some cluster restructuring, but core preserved
    BIG = "big"  # Major restructuring, many new/removed clusters


@dataclass
class ClusterMatch:
    """Represents a match between an old and new cluster."""

    old_cluster_id: int
    new_cluster_id: int
    similarity: float  # Jaccard similarity (0.0 to 1.0)
    old_node_count: int
    new_node_count: int
    common_nodes: Set[str]


@dataclass
class ChangeMetrics:
    """Detailed metrics about cluster changes."""

    total_old_nodes: int = 0
    total_new_nodes: int = 0
    nodes_moved: int = 0  # Nodes that changed clusters
    nodes_added: int = 0  # New nodes not in any old cluster
    nodes_removed: int = 0  # Old nodes not in any new cluster
    matched_clusters: int = 0
    new_clusters: int = 0
    removed_clusters: int = 0
    avg_similarity: float = 0.0
    max_similarity: float = 0.0
    min_similarity: float = 0.0

    @property
    def node_movement_ratio(self) -> float:
        """Ratio of nodes that moved relative to total nodes."""
        total = max(self.total_old_nodes, self.total_new_nodes)
        return self.nodes_moved / total if total > 0 else 0.0


@dataclass
class ClusterChangeResult:
    """Complete result of cluster change analysis."""

    classification: ChangeClassification
    matched_clusters: List[ClusterMatch] = field(default_factory=list)
    new_clusters: List[int] = field(default_factory=list)
    removed_clusters: List[int] = field(default_factory=list)
    metrics: ChangeMetrics = field(default_factory=ChangeMetrics)
    language: str = ""

    def __str__(self) -> str:
        return (
            f"ClusterChangeResult({self.language}): {self.classification.value}, "
            f"matched={len(self.matched_clusters)}, new={len(self.new_clusters)}, "
            f"removed={len(self.removed_clusters)}, movement={self.metrics.node_movement_ratio:.2%}"
        )


class ClusterChangeAnalyzer:
    """
    Analyzes changes between two cluster results from different analysis runs.

    This class provides methods to:
    - Match clusters between old and new analysis using Jaccard similarity
    - Identify new and removed clusters
    - Calculate detailed change metrics
    - Classify the magnitude of changes (Small/Medium/Big)
    """

    # Threshold for considering two clusters as matching (50% overlap)
    SIMILARITY_THRESHOLD = 0.5

    # Classification thresholds - based on cluster creation/removal, not node movement
    # SMALL: No new or removed clusters (only reassignments)
    # MEDIUM: Some new or removed clusters (may need new components)
    # BIG: Major restructuring (excessive new AND removed clusters)
    MEDIUM_CLUSTER_CHANGE_THRESHOLD = 3  # >=3 new or removed clusters = medium/big

    def __init__(self, similarity_threshold: float | None = None):
        """
        Initialize the analyzer.

        Args:
            similarity_threshold: Minimum Jaccard similarity to consider clusters as matching.
                                 Defaults to 0.5 if not provided.
        """
        self.similarity_threshold = similarity_threshold or self.SIMILARITY_THRESHOLD

    def analyze_changes(
        self, old_clusters: ClusterResult, new_clusters: ClusterResult, language: str = ""
    ) -> ClusterChangeResult:
        """
        Analyze changes between old and new cluster results.

        Args:
            old_clusters: Cluster result from the previous analysis
            new_clusters: Cluster result from the current analysis
            language: Optional language identifier for logging

        Returns:
            ClusterChangeResult with detailed change information and classification
        """
        logger.info(f"[{language}] Analyzing cluster changes...")

        # Match clusters using Jaccard similarity
        matches, unmatched_old, unmatched_new = self._match_clusters(old_clusters, new_clusters)

        # Calculate metrics
        metrics = self._calculate_metrics(old_clusters, new_clusters, matches)

        # Classify the change
        classification = self._classify_change(matches, unmatched_old, unmatched_new, metrics)

        result = ClusterChangeResult(
            classification=classification,
            matched_clusters=matches,
            new_clusters=sorted(unmatched_new),
            removed_clusters=sorted(unmatched_old),
            metrics=metrics,
            language=language,
        )

        logger.info(f"[{language}] {result}")
        return result

    def _match_clusters(
        self, old_clusters: ClusterResult, new_clusters: ClusterResult
    ) -> Tuple[List[ClusterMatch], Set[int], Set[int]]:
        """
        Match clusters between old and new results using Jaccard similarity.

        Uses a greedy matching algorithm:
        1. Calculate similarity for all old-new cluster pairs
        2. Sort by similarity (descending)
        3. Greedily assign matches above threshold

        Args:
            old_clusters: Previous cluster result
            new_clusters: Current cluster result

        Returns:
            Tuple of (matches, unmatched_old_ids, unmatched_new_ids)
        """
        matches: List[ClusterMatch] = []
        matched_old: Set[int] = set()
        matched_new: Set[int] = set()

        # Calculate all pairwise similarities
        similarities: List[Tuple[int, int, float, Set[str]]] = []

        for old_id in old_clusters.get_cluster_ids():
            old_nodes = old_clusters.get_nodes_for_cluster(old_id)

            for new_id in new_clusters.get_cluster_ids():
                new_nodes = new_clusters.get_nodes_for_cluster(new_id)

                # Calculate Jaccard similarity: |A ∩ B| / |A ∪ B|
                intersection = old_nodes & new_nodes
                union = old_nodes | new_nodes

                if union:
                    similarity = len(intersection) / len(union)
                    if similarity >= self.similarity_threshold:
                        similarities.append((old_id, new_id, similarity, intersection))

        # Sort by similarity (descending) for greedy matching
        similarities.sort(key=lambda x: x[2], reverse=True)

        # Greedily assign matches
        for old_id, new_id, similarity, intersection in similarities:
            if old_id not in matched_old and new_id not in matched_new:
                old_nodes = old_clusters.get_nodes_for_cluster(old_id)
                new_nodes = new_clusters.get_nodes_for_cluster(new_id)

                match = ClusterMatch(
                    old_cluster_id=old_id,
                    new_cluster_id=new_id,
                    similarity=similarity,
                    old_node_count=len(old_nodes),
                    new_node_count=len(new_nodes),
                    common_nodes=intersection,
                )
                matches.append(match)
                matched_old.add(old_id)
                matched_new.add(new_id)

        # Identify unmatched clusters
        unmatched_old = old_clusters.get_cluster_ids() - matched_old
        unmatched_new = new_clusters.get_cluster_ids() - matched_new

        logger.debug(f"Matched {len(matches)} clusters, {len(unmatched_old)} removed, {len(unmatched_new)} new")

        return matches, unmatched_old, unmatched_new

    def _calculate_metrics(
        self,
        old_clusters: ClusterResult,
        new_clusters: ClusterResult,
        matches: List[ClusterMatch],
    ) -> ChangeMetrics:
        """
        Calculate detailed change metrics.

        Args:
            old_clusters: Previous cluster result
            new_clusters: Current cluster result
            matches: List of matched clusters

        Returns:
            ChangeMetrics with detailed statistics
        """
        metrics = ChangeMetrics()

        # Collect all nodes
        all_old_nodes: Set[str] = set()
        all_new_nodes: Set[str] = set()

        for cluster_id in old_clusters.get_cluster_ids():
            all_old_nodes.update(old_clusters.get_nodes_for_cluster(cluster_id))

        for cluster_id in new_clusters.get_cluster_ids():
            all_new_nodes.update(new_clusters.get_nodes_for_cluster(cluster_id))

        metrics.total_old_nodes = len(all_old_nodes)
        metrics.total_new_nodes = len(all_new_nodes)
        metrics.nodes_added = len(all_new_nodes - all_old_nodes)
        metrics.nodes_removed = len(all_old_nodes - all_new_nodes)

        # Count nodes that moved (changed cluster membership)
        nodes_in_matched_old: Set[str] = set()
        nodes_in_matched_new: Set[str] = set()

        for match in matches:
            old_nodes = old_clusters.get_nodes_for_cluster(match.old_cluster_id)
            new_nodes = new_clusters.get_nodes_for_cluster(match.new_cluster_id)
            nodes_in_matched_old.update(old_nodes)
            nodes_in_matched_new.update(new_nodes)
            metrics.nodes_moved += len(old_nodes - new_nodes) + len(new_nodes - old_nodes)

        # Nodes in unmatched clusters count as moved
        for old_id in old_clusters.get_cluster_ids():
            if old_id not in [m.old_cluster_id for m in matches]:
                metrics.nodes_moved += len(old_clusters.get_nodes_for_cluster(old_id))

        for new_id in new_clusters.get_cluster_ids():
            if new_id not in [m.new_cluster_id for m in matches]:
                metrics.nodes_moved += len(new_clusters.get_nodes_for_cluster(new_id))

        # Cluster statistics
        metrics.matched_clusters = len(matches)
        metrics.new_clusters = len(new_clusters.get_cluster_ids()) - len(matches)
        metrics.removed_clusters = len(old_clusters.get_cluster_ids()) - len(matches)

        # Similarity statistics
        if matches:
            similarities = [m.similarity for m in matches]
            metrics.avg_similarity = sum(similarities) / len(similarities)
            metrics.max_similarity = max(similarities)
            metrics.min_similarity = min(similarities)

        return metrics

    def _classify_change(
        self,
        matches: List[ClusterMatch],
        unmatched_old: Set[int],
        unmatched_new: Set[int],
        metrics: ChangeMetrics,
    ) -> ChangeClassification:
        """
        Classify the magnitude of cluster changes based on cluster creation/removal.

        Classification criteria:
        - SMALL: No new or removed clusters (only matched clusters with file reassignments)
        - MEDIUM: Some new OR removed clusters (1-2 changes, may need new components)
        - BIG: Major restructuring (3+ new AND 3+ removed clusters)

        Args:
            matches: List of matched clusters
            unmatched_old: Set of removed cluster IDs
            unmatched_new: Set of new cluster IDs
            metrics: Change metrics (for logging, not classification)

        Returns:
            ChangeClassification enum value
        """
        new_clusters = len(unmatched_new)
        removed_clusters = len(unmatched_old)
        total_changes = new_clusters + removed_clusters

        logger.debug(f"Classification check: new_clusters={new_clusters}, removed_clusters={removed_clusters}")

        # SMALL: No new or removed clusters - just reassignments
        if new_clusters == 0 and removed_clusters == 0:
            logger.info(f"Classified as SMALL: no new or removed clusters (only reassignments)")
            return ChangeClassification.SMALL

        # BIG: Major restructuring (excessive new AND removed clusters)
        if (
            new_clusters >= self.MEDIUM_CLUSTER_CHANGE_THRESHOLD
            and removed_clusters >= self.MEDIUM_CLUSTER_CHANGE_THRESHOLD
        ):
            logger.info(f"Classified as BIG: major restructuring ({new_clusters} new, {removed_clusters} removed)")
            return ChangeClassification.BIG

        # MEDIUM: Some new or removed clusters (but not major restructuring)
        logger.info(f"Classified as MEDIUM: {new_clusters} new, {removed_clusters} removed clusters")
        return ChangeClassification.MEDIUM


def analyze_cluster_changes_for_languages(
    old_cluster_results: Dict[str, ClusterResult],
    new_cluster_results: Dict[str, ClusterResult],
    similarity_threshold: float | None = None,
) -> Dict[str, ClusterChangeResult]:
    """
    Analyze cluster changes for multiple languages.

    Args:
        old_cluster_results: Dict mapping language -> old ClusterResult
        new_cluster_results: Dict mapping language -> new ClusterResult
        similarity_threshold: Optional similarity threshold for matching

    Returns:
        Dict mapping language -> ClusterChangeResult
    """
    analyzer = ClusterChangeAnalyzer(similarity_threshold)
    results: Dict[str, ClusterChangeResult] = {}

    # Analyze all languages present in either old or new results
    all_languages = set(old_cluster_results.keys()) | set(new_cluster_results.keys())

    for language in all_languages:
        old_clusters = old_cluster_results.get(language)
        new_clusters = new_cluster_results.get(language)

        if old_clusters is None:
            logger.warning(f"[{language}] No old cluster results, treating as all new")
            # Create empty result for comparison
            old_clusters = ClusterResult()

        if new_clusters is None:
            logger.warning(f"[{language}] No new cluster results, treating as all removed")
            new_clusters = ClusterResult()

        result = analyzer.analyze_changes(old_clusters, new_clusters, language)
        results[language] = result

    return results


def get_overall_classification(results: Dict[str, ClusterChangeResult]) -> ChangeClassification:
    """
    Get the overall classification across all languages (worst-case).

    Args:
        results: Dict mapping language -> ClusterChangeResult

    Returns:
        The most severe classification found
    """
    if not results:
        return ChangeClassification.SMALL

    # Priority: BIG > MEDIUM > SMALL
    for classification in [ChangeClassification.BIG, ChangeClassification.MEDIUM]:
        if any(r.classification == classification for r in results.values()):
            return classification

    return ChangeClassification.SMALL
