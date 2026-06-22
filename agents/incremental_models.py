from dataclasses import dataclass, field
from enum import StrEnum

from agents.agent_responses import Component
from agents.cluster_ids import GraphClusterId


@dataclass
class IncrementalUpdatePlan:
    """Deterministic work plan produced by incremental stitching."""

    refresh_ids: set[str] = field(default_factory=set)
    new_component_ids: set[str] = field(default_factory=set)


class Verdict(StrEnum):
    """Derived stitching verdict, inferred from route metadata."""

    ADD = "ADD"
    UPDATE = "UPDATE"
    NOOP = "NOOP"


class RouteBucketKind(StrEnum):
    """How a cluster group should be emitted after route stabilization."""

    ADD = "add"
    ORIGINAL = "original"
    REROUTED = "rerouted"


@dataclass(frozen=True)
class ClusterRouteBucket:
    """Bucket key for clusters that share the same stabilized route."""

    kind: RouteBucketKind
    destination_id: str = ""


@dataclass
class ExistingComponentOwnership:
    methods: dict[str, Component] = field(default_factory=dict)
    files: dict[str, list[Component]] = field(default_factory=dict)


@dataclass
class DeltaClusterContents:
    members: dict[GraphClusterId, set[str]] = field(default_factory=dict)
    files: dict[GraphClusterId, set[str]] = field(default_factory=dict)
