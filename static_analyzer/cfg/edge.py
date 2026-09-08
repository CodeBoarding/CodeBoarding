"""Call-graph edge types: call edges and the non-call reference edges."""

from __future__ import annotations

from collections.abc import Callable, Hashable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import NotRequired, TypedDict

from static_analyzer.node import Node


class EdgeKind(StrEnum):
    """Kind of an edge: a call, or one of the structural relationships a call graph misses.

    CALL is what ``CallGraph.edges`` holds. The rest are ``ReferenceEdge`` kinds: a method
    belongs to its class (CONTAINS), a class extends another (INHERITS), code names a type
    (TYPEREF), a module imports another (IMPORT).
    """

    CALL = "call"
    CONTAINS = "contains"
    INHERITS = "inherits"
    TYPEREF = "typeref"
    IMPORT = "import"

    @property
    def relation_label(self) -> str:
        """The verb a relation gets when edges of this kind alone connect two components."""
        return _RELATION_LABEL_BY_KIND[self]


_RELATION_LABEL_BY_KIND: dict[EdgeKind, str] = {
    EdgeKind.CALL: "calls",
    EdgeKind.CONTAINS: "contains",
    EdgeKind.INHERITS: "inherits from",
    EdgeKind.TYPEREF: "uses",
    EdgeKind.IMPORT: "imports",
}

# What structural consumers fold into ``to_networkx`` on top of call edges. The call graph
# leaves ~a fifth of symbols isolated (constructors, dunders, DI/interface methods), so
# completing it with these avoids grab-bag components. TYPEREF is derived from source once
# the per-project graphs merge (``engine.type_reference_builder``); IMPORT has no producer
# yet and is expected to over-merge (coarse, dense, file-level) when one does.
DEFAULT_REFERENCE_KINDS: tuple[EdgeKind, ...] = (EdgeKind.CONTAINS, EdgeKind.INHERITS)
# The reference kinds that, like a call, make one component depend on another.
RELATION_REFERENCE_KINDS: tuple[EdgeKind, ...] = (EdgeKind.INHERITS, EdgeKind.TYPEREF)


class CallSiteLocation(TypedDict):
    """A one-based source location where an edge occurs: a call, or a type being named."""

    line: int
    file: NotRequired[str]
    column: NotRequired[int]


@dataclass(frozen=True)
class ReferenceEdge:
    """A non-call relationship between two qualified names, and where the source names the target.

    Two edges are the same edge when they join the same names the same way; the sites are what
    the source wrote and never decide identity.
    """

    src: str
    dst: str
    kind: EdgeKind
    sites: tuple[CallSiteLocation, ...] = field(default=(), compare=False)

    def __post_init__(self) -> None:
        if self.kind is EdgeKind.CALL:
            raise ValueError("a call is an Edge with call sites, not a ReferenceEdge")


class Edge:
    def __init__(self, src_node: Node, dst_node: Node, call_sites: Sequence[Mapping[str, Hashable]] = ()) -> None:
        self.src_node = src_node
        self.dst_node = dst_node
        self._call_sites: list[CallSiteLocation] = []
        self._call_site_keys: set[tuple[tuple[str, Hashable], ...]] = set()
        for site in call_sites:
            self.add_call_site(site)

    @property
    def call_sites(self) -> list[CallSiteLocation]:
        return [site.copy() for site in self._call_sites]

    def get_source(self) -> str:
        return self.src_node.fully_qualified_name

    def get_destination(self) -> str:
        return self.dst_node.fully_qualified_name

    def __repr__(self) -> str:
        return f"Edge({self.src_node.fully_qualified_name} -> {self.dst_node.fully_qualified_name})"

    def add_call_site(self, call_site: Mapping[str, Hashable]) -> None:
        call_site = self._normalize_call_site(call_site)
        call_site_key = tuple(sorted(call_site.items()))
        if call_site_key not in self._call_site_keys:
            self._call_site_keys.add(call_site_key)
            self._call_sites.append(call_site)

    def visit_paths(self, fn: Callable[[str], str]) -> None:
        for site in self._call_sites:
            if "file" in site:
                site["file"] = fn(str(site["file"]))
        self._call_site_keys = {tuple(sorted(site.items())) for site in self._call_sites}

    @staticmethod
    def _normalize_call_site(call_site: Mapping[str, Hashable]) -> CallSiteLocation:
        file = call_site.get("file", call_site.get("file_path"))
        line = call_site.get("line")
        column = call_site.get("column")
        if not isinstance(line, int):
            raise ValueError("Call sites require a one-based integer line")
        location = CallSiteLocation(line=line)
        if file is not None:
            if not isinstance(file, str):
                raise ValueError("Call-site files must be strings")
            location["file"] = file
        if column is not None:
            if not isinstance(column, int):
                raise ValueError("Call-site columns must be one-based integers")
            location["column"] = column
        return location
