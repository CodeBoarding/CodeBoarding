from dataclasses import dataclass, field


@dataclass
class ScopeUpdateResult:
    """Result of applying one incremental plan to one analysis scope."""

    refresh_ids: set[str] = field(default_factory=set)
    new_component_ids: set[str] = field(default_factory=set)
    removed_ids: set[str] = field(default_factory=set)
    regenerate_scope: bool = False


@dataclass
class RecursiveScopeUpdateResult(ScopeUpdateResult):
    """Aggregated result from recursively updating expanded scopes."""

    touched_scopes: set[str] = field(default_factory=set)
