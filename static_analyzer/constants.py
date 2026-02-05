"""Constants for the static analyzer module."""


class ClusteringConfig:
    """Configuration constants for graph clustering algorithms.

    These values are based on empirical testing with codebases ranging from
    100-10,000 nodes. They balance clustering quality with computational efficiency.
    """

    # Default clustering parameters - chosen to work well for typical codebases (500-2000 nodes)
    DEFAULT_TARGET_CLUSTERS = 20  # Sweet spot for human comprehension and LLM context
    DEFAULT_MIN_CLUSTER_SIZE = 2  # Avoid singleton clusters that don't show relationships

    # Quality thresholds for determining "good" clustering
    MIN_COVERAGE_RATIO = 0.75  # At least 75% of nodes should be in meaningful clusters
    MAX_SINGLETON_RATIO = 0.6  # No more than 60% singleton clusters (indicates poor clustering)
    MIN_CLUSTER_COUNT_RATIO = 6  # Minimum clusters = target_clusters // 6 (avoid too few clusters)
    MAX_CLUSTER_COUNT_MULTIPLIER = 2  # Maximum clusters = target_clusters * 2

    # Cluster size constraints
    SMALL_GRAPH_MAX_CLUSTER_RATIO = 0.6  # For graphs < 50 nodes, max cluster can be 60% of total
    LARGE_GRAPH_MAX_CLUSTER_RATIO = 0.4  # For larger graphs, max cluster should be 40% of total
    MAX_SIZE_TO_AVG_RATIO = 8  # Largest cluster shouldn't be more than 8x average size
    SMALL_GRAPH_THRESHOLD = 50  # Threshold between "small" and "large" graphs

    # Cluster balancing parameters
    MIN_CLUSTER_SIZE_MULTIPLIER = 3  # When merging, stop at min_size * 3 to avoid oversized clusters
    MAX_CLUSTER_SIZE_MULTIPLIER = 3  # Max cluster size = (total_nodes // target_clusters) * 3
    MIN_MAX_CLUSTER_SIZE = 10  # Absolute minimum for max cluster size

    # Display limits
    MAX_DISPLAY_CLUSTERS = 25  # Maximum clusters to show in output (readability limit)

    # Language-specific delimiters for qualified names
    DEFAULT_DELIMITER = "."  # Works for Python, Java, C#
    DELIMITER_MAP = {
        "python": ".",
        "go": ".",
        "php": "\\",  # PHP uses backslash for namespaces
        "typescript": ".",
        "javascript": ".",
    }


class NodeType:
    # Node type constants
    METHOD_TYPE = 6


class GraphConfig:
    # Deterministic seed for clustering algorithms
    CLUSTERING_SEED = 42
