"""Call-graph edge types: call edges and the non-call reference edges."""

from __future__ import annotations

from collections.abc import Callable, Hashable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum

from static_analyzer.node import Node


class EdgeKind(StrEnum):
    """Kind of a *reference* edge — the structural relationships a call graph misses.

    A method belongs to its class (CONTAINS), a class extends another (INHERITS),
    code names a type (TYPEREF), a module imports another (IMPORT). Call edges are
    not listed: they live in ``CallGraph.edges`` and carry no kind tag.
    """

    CONTAINS = "contains"
    INHERITS = "inherits"
    TYPEREF = "typeref"
    IMPORT = "import"


@dataclass(frozen=True)
class ReferenceEdge:
    """A non-call relationship between two qualified names."""

    src: str
    dst: str
    kind: EdgeKind


class Edge:
    def __init__(self, src_node: Node, dst_node: Node, call_sites: Sequence[Mapping[str, Hashable]] = ()) -> None:
        self.src_node = src_node
        self.dst_node = dst_node
        self._call_sites: list[dict[str, Hashable]] = []
        self._call_site_keys: set[tuple[tuple[str, Hashable], ...]] = set()
        for site in call_sites:
            self.add_call_site(site)

    @property
    def call_sites(self) -> list[dict[str, Hashable]]:
        return [dict(site) for site in self._call_sites]

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

    @staticmethod
    def _normalize_call_site(call_site: Mapping[str, Hashable]) -> dict[str, Hashable]:
        normalized = dict(call_site)
        if "file" not in normalized and "file_path" in normalized:
            normalized["file"] = normalized.pop("file_path")
        return normalized

    def visit_paths(self, fn: Callable[[str], str]) -> None:
        for site in self._call_sites:
            if "file" in site:
                site["file"] = fn(str(site["file"]))
        self._call_site_keys = {tuple(sorted(site.items())) for site in self._call_sites}
