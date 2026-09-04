"""Derived component ownership data used to resolve relation endpoints."""

from __future__ import annotations

from dataclasses import dataclass

from agents.agent_responses import AnalysisInsights, Component, SourceCodeReference


@dataclass(frozen=True)
class ComponentOwnershipIndex:
    """Resolve source references to their unique owning component."""

    candidates_by_leaf: dict[str, list[tuple[str, str]]]

    @classmethod
    def from_analysis(cls, analysis: AnalysisInsights) -> ComponentOwnershipIndex:
        candidates_by_leaf: dict[str, list[tuple[str, str]]] = {}
        for component in analysis.components:
            for file_methods in component.file_methods:
                for method in file_methods.methods:
                    candidates_by_leaf.setdefault(method.qualified_name.rsplit(".", 1)[-1], []).append(
                        (method.qualified_name, component.component_id)
                    )
        return cls(candidates_by_leaf)

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
