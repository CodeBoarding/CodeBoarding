"""Call-graph edge types: call edges and the non-call reference edges."""

from __future__ import annotations

from collections.abc import Callable, Hashable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import NotRequired, TypedDict

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


# What structural consumers fold into ``to_networkx`` on top of call edges. The call graph
# leaves ~a fifth of symbols isolated (constructors, dunders, DI/interface methods), so
# completing it with these avoids grab-bag components. TYPEREF and IMPORT are plumbed through
# ``LanguageAnalysisResult`` but no engine emits them yet, and IMPORT is expected to over-merge
# (coarse, dense, file-level) when one does.
DEFAULT_REFERENCE_KINDS: tuple[EdgeKind, ...] = (EdgeKind.CONTAINS, EdgeKind.INHERITS)


@dataclass(frozen=True)
class ReferenceEdge:
    """A non-call relationship between two qualified names."""

    src: str
    dst: str
    kind: EdgeKind


class CallSiteLocation(TypedDict):
    """A one-based source location where a call occurs."""

    line: int
    file: NotRequired[str]
    column: NotRequired[int]


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
        normalized = dict(call_site)
        if "file" not in normalized and "file_path" in normalized:
            normalized["file"] = normalized.pop("file_path")
        file = normalized.get("file")
        line = normalized.get("line")
        column = normalized.get("column")
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
