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

# 16 hex chars = 64 bits of SHA-256. The content hash only flags whether a method
# body or file changed between two analyses — not a security or uniqueness
# guarantee — so 64 bits is ample (collisions are astronomically unlikely across a
# repo's ~10^4-10^5 entries) while keeping analysis.json compact.
CONTENT_HASH_LENGTH = 16
