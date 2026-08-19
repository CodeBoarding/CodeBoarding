"""Configuration for grouping leaf clusters into components."""

from dataclasses import dataclass

from static_analyzer.constants import ClusteringConfig


@dataclass(frozen=True)
class GroupingConfig:
    min_components: int
    max_components: int
    seed: int = ClusteringConfig.CLUSTERING_SEED
    drift_budget: float = 0.10
    resolutions: tuple[float, ...] = (0.25, 0.5, 0.75, 1.0, 1.25, 1.5, 2.0, 2.5, 3.0, 4.0, 5.0, 7.0, 10.0)


DEFAULT_GROUPING_CONFIG = GroupingConfig(min_components=5, max_components=8)
SUBCOMPONENT_GROUPING_CONFIG = GroupingConfig(min_components=3, max_components=8)
