"""Cluster snapshot entry used by seeded subcomponent clustering (``cluster_delta``)."""

from dataclasses import dataclass, field


@dataclass
class ClusterSnapshotEntry:
    members: set[str] = field(default_factory=set)
    files: set[str] = field(default_factory=set)
    member_files: dict[str, str] = field(default_factory=dict)
