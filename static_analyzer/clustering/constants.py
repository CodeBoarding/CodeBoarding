"""Tuning constants of the clustering stage."""

# Marker on a ClusterResult whose clusters are synthetic one-method-per-cluster
# groups, produced when a subgraph had too few natural clusters to assign methods
# at a useful granularity. Its modularity is not comparable to a real clustering's.
METHOD_LEVEL_STRATEGY = "method_level_expansion"

# How many top-level components the deterministic grouping may produce. The
# count is decided by the modularity peak over this range, not by the LLM — so
# the component structure is stable across re-runs.
TOP_LEVEL_COMPONENTS_MIN = 5
TOP_LEVEL_COMPONENTS_MAX = 8

# Same idea for a component's sub-components (one level down); a component is
# usually smaller than the whole repo, so the floor is lower.
SUBCOMPONENTS_MIN = 3
SUBCOMPONENTS_MAX = 8

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
