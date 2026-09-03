"""The tree specification: the decisions a full run makes and every later run replays."""

from __future__ import annotations

from collections.abc import Collection, Iterable, Mapping
from dataclasses import dataclass, field, replace
from typing import Any

from clustering_ids import ROOT_SCOPE_ID, CodeBoardingClusterIds, ScopeId

Prefix = tuple[str, ...]

COMPONENT = "component"
UNPLACED = "unplaced"
"""Kinds of rule. The unplaced bucket owns what no rule claims, so it is drawn rather than
hidden and can never collide with a component the planner proposes."""


@dataclass(frozen=True)
class ComponentRule:
    """What a component owns: prefixes of the trie, words of the names, and a last resort.

    Replay tries the longest matching ``prefixes`` entry, then a weighted vote over
    ``terms``, then the longest matching ``fallback_prefixes`` entry. ``parts`` are the
    candidates a grouping merged into this rule; the un-merge rung splits them back out.
    """

    component_id: str
    name: str
    prefixes: tuple[Prefix, ...] = ()
    terms: tuple[str, ...] = ()
    fallback_prefixes: tuple[Prefix, ...] = ()
    parts: tuple[ComponentRule, ...] = ()
    origin: str = "frontier"
    kind: str = COMPONENT

    @property
    def is_fallback_only(self) -> bool:
        """A rule that claims units only as a last resort: loose files, a layer's residue."""
        return not self.prefixes and not self.terms

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"id": self.component_id, "name": self.name, "origin": self.origin, "kind": self.kind}
        if self.prefixes:
            out["prefixes"] = [list(prefix) for prefix in self.prefixes]
        if self.terms:
            out["terms"] = list(self.terms)
        if self.fallback_prefixes:
            out["fallback_prefixes"] = [list(prefix) for prefix in self.fallback_prefixes]
        if self.parts:
            out["parts"] = [part.to_dict() for part in self.parts]
        return out

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> ComponentRule:
        return cls(
            component_id=str(raw.get("id", "")),
            name=str(raw.get("name", "")),
            prefixes=tuple(tuple(prefix) for prefix in raw.get("prefixes", ())),
            terms=tuple(raw.get("terms", ())),
            fallback_prefixes=tuple(tuple(prefix) for prefix in raw.get("fallback_prefixes", ())),
            parts=tuple(cls.from_dict(part) for part in raw.get("parts", ())),
            origin=str(raw.get("origin", "frontier")),
            kind=str(raw.get("kind", COMPONENT)),
        )


@dataclass
class ScopeSpec:
    """The rules of one scope, and which rung of the ladder produced them.

    No rules means the scope is a leaf: ``leaf_reason`` says why the names ran out.
    """

    scope_id: ScopeId
    rules: list[ComponentRule] = field(default_factory=list)
    axis: str = ""
    rung: str = ""
    leaf_reason: str = ""
    last_id: int = 0
    """The highest local id this scope ever allocated, so a retired component's id is never reissued."""

    @property
    def is_leaf(self) -> bool:
        return not self.rules

    @property
    def components(self) -> list[ComponentRule]:
        return [rule for rule in self.rules if rule.kind == COMPONENT]

    @property
    def unplaced_rule(self) -> ComponentRule | None:
        return next((rule for rule in self.rules if rule.kind == UNPLACED), None)

    def rule(self, component_id: str) -> ComponentRule | None:
        return next((rule for rule in self.rules if rule.component_id == component_id), None)

    def next_id(self, taken: Collection[str] = ()) -> str:
        """A fresh local id: above every id this scope uses or ``taken`` names, never a gap refilled."""
        prefix = CodeBoardingClusterIds.prefix_for_scope(self.scope_id)
        used = {rule.component_id for rule in self.rules} | set(taken)
        local = [component_id.rpartition(".")[2] for component_id in used]
        self.last_id = max(self.last_id, max((int(part) for part in local if part.isdigit()), default=0)) + 1
        return CodeBoardingClusterIds.qualify_local_id(str(self.last_id), prefix)

    def to_dict(self) -> dict[str, Any]:
        return {
            "axis": self.axis,
            "rung": self.rung,
            "leaf_reason": self.leaf_reason,
            "last_id": self.last_id,
            "rules": [rule.to_dict() for rule in self.rules],
        }

    @classmethod
    def from_dict(cls, scope_id: ScopeId, raw: Mapping[str, Any]) -> ScopeSpec:
        return cls(
            scope_id=scope_id,
            rules=[ComponentRule.from_dict(rule) for rule in raw.get("rules", ())],
            axis=str(raw.get("axis", "")),
            rung=str(raw.get("rung", "")),
            leaf_reason=str(raw.get("leaf_reason", "")),
            last_id=int(raw.get("last_id", 0)),
        )


SPEC_VERSION = 2
"""2: ``last_id`` per scope; a spec without it cannot keep a retired id from being reissued."""


@dataclass
class TreeSpec:
    """Every scope's rules plus the per-repo machinery words, written once per full run."""

    scopes: dict[ScopeId, ScopeSpec] = field(default_factory=dict)
    machinery: frozenset[str] = frozenset()
    grouper: str = ""
    version: int = SPEC_VERSION

    def scope(self, scope_id: ScopeId) -> ScopeSpec | None:
        return self.scopes.get(scope_id)

    def set_scope(self, scope: ScopeSpec) -> None:
        self.scopes[scope.scope_id] = scope

    def reroot(self, absorbed_ids: Iterable[ScopeId]) -> None:
        """Follow the save-time absorption of a single child: its rules become its parent's, ids below move up."""
        for child_id in absorbed_ids:
            parent_id = child_id.rpartition(".")[0]
            child = self.scopes.pop(child_id, None)
            if child is None:
                continue
            prefix = f"{child_id}."

            def moved(identifier: str) -> str:
                if not identifier.startswith(prefix):
                    return identifier
                tail = identifier.removeprefix(prefix)
                return f"{parent_id}.{tail}" if parent_id else tail

            scopes: dict[ScopeId, ScopeSpec] = {}
            for scope_id, scope in self.scopes.items():
                under_parent = scope_id.startswith(f"{parent_id}.") if parent_id else scope_id != ROOT_SCOPE_ID
                if under_parent and not scope_id.startswith(prefix):
                    # A retired sibling of the absorbed child: its id may be reissued, so its scope must go.
                    continue
                scope.scope_id = moved(scope_id)
                scope.rules = [replace(rule, component_id=moved(rule.component_id)) for rule in scope.rules]
                scopes[scope.scope_id] = scope
            parent_scope_id = parent_id or ROOT_SCOPE_ID
            scopes[parent_scope_id] = ScopeSpec(
                parent_scope_id,
                [replace(rule, component_id=moved(rule.component_id)) for rule in child.rules],
                child.axis,
                child.rung,
                child.leaf_reason,
                child.last_id,
            )
            self.scopes = scopes

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "grouper": self.grouper,
            "machinery": sorted(self.machinery),
            "scopes": {scope_id: self.scopes[scope_id].to_dict() for scope_id in self._tree_order()},
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> TreeSpec:
        return cls(
            scopes={
                scope_id: ScopeSpec.from_dict(scope_id, scope) for scope_id, scope in raw.get("scopes", {}).items()
            },
            machinery=frozenset(raw.get("machinery", ())),
            grouper=str(raw.get("grouper", "")),
            version=int(raw.get("version", SPEC_VERSION)),
        )

    def _tree_order(self) -> list[ScopeId]:
        rest = CodeBoardingClusterIds.sort({scope_id for scope_id in self.scopes if not is_root(scope_id)})
        return [ROOT_SCOPE_ID, *rest] if ROOT_SCOPE_ID in self.scopes else rest


def is_root(scope_id: ScopeId) -> bool:
    return scope_id == ROOT_SCOPE_ID
