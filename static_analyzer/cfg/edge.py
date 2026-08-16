"""Call-graph edge types: call edges and the kinds a reference edge can carry."""

from collections.abc import Callable, Hashable, Mapping, Sequence
from enum import StrEnum

from static_analyzer.node import Node


class EdgeKind(StrEnum):
    """Kind of relationship an edge represents.

    ``CALL`` edges live in ``CallGraph.edges`` and drive component *relations*.
    The rest are *reference edges* (``CallGraph.reference_edges``): structural
    relationships the pure call graph misses — a method belongs to its class
    (CONTAINS), a class extends another (INHERITS), code names a type (TYPEREF),
    a module imports another (IMPORT). They complete the graph for *clustering*
    (so constructors/dunders/DI/interface methods aren't graph-isolated) without
    polluting the call-relation semantics.
    """

    CALL = "call"
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
