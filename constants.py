"""Application-level constants for CodeBoarding."""


class AppConfig:
    MAX_CONCURRENT_JOBS = 5
    DEFAULT_REPO_ROOT = "./repos"
    DEFAULT_ROOT_RESULT = "./results"
    DEFAULT_LLM_SIZE_LIMIT = 2_500_000


# Minimum number of clusters needed for meaningful component decomposition.
# If a subgraph has fewer clusters than this threshold, we expand to method-level
# clustering (each method becomes its own cluster) to ensure fine-grained assignment.
MIN_CLUSTERS_THRESHOLD = 5
