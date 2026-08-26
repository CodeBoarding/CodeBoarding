"""Derived component ownership data used to resolve relation endpoints."""

from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass

from agents.agent_responses import Component, SourceCodeReference
from static_analyzer.clustering import ClusterScopeResult


def group_ids_by_name(components: list[Component], valid_group_ids: Collection[str]) -> dict[str, str]:
    """Map component source-group names to their authoritative live group IDs."""
    valid_ids = set(valid_group_ids)
    return {
        name: component.component_id
        for component in components
        if component.component_id in valid_ids
        for name in component.source_group_names
    }


@dataclass(frozen=True)
class ComponentOwnershipIndex:
    """Resolve source references to their unique owning component."""

    candidates_by_leaf: dict[str, list[tuple[str, str]]]

    @classmethod
    def from_clustering_hierarchy(cls, hierarchy: ClusterScopeResult) -> ComponentOwnershipIndex:
        """Index every symbol at every depth against the deepest group that owns it.

        Why: component IDs are group IDs, so the hierarchy already knows the owner of every
        symbol in the repo, not only the ones in the scope an agent happens to be naming. A
        symbol belongs to its leaf group and to every ancestor of it, so a flat union over
        all depths would leave almost every symbol with several owners and blank out
        ``owner_of``; the deepest group is the single answer, the same one
        ``build_global_node_to_component_map`` gives for the expanded frontier.
        """
        owner_by_symbol: dict[str, str] = {}
        for group_id, group in sorted(hierarchy.clustering_groups.items(), key=lambda item: item[0].count(".")):
            for qualified_name in group.qualified_names:
                owner_by_symbol[qualified_name] = group_id
        return cls.from_node_owners(owner_by_symbol)

    @classmethod
    def from_node_owners(cls, node_owners: dict[str, str]) -> ComponentOwnershipIndex:
        candidates_by_leaf: dict[str, list[tuple[str, str]]] = {}
        for qualified_name, component_id in node_owners.items():
            candidates_by_leaf.setdefault(qualified_name.rsplit(".", 1)[-1], []).append((qualified_name, component_id))
        return cls(candidates_by_leaf)

    def owner_of(self, reference: SourceCodeReference) -> str:
        """Return the unique owner of a canonical or suffix-qualified reference."""
        qualified_name = reference.qualified_name.replace(":", ".")
        owners = {
            component_id
            for candidate, component_id in self.candidates_by_leaf.get(qualified_name.rsplit(".", 1)[-1], ())
            if qualified_name == candidate.replace(":", ".")
            or qualified_name.endswith(f".{candidate.replace(':', '.')}")
            or candidate.replace(":", ".").endswith(f".{qualified_name}")
        }
        return owners.pop() if len(owners) == 1 else ""
