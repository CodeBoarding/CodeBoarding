"""Call-graph edge types: call edges, the non-call reference edges, and what each kind means."""

from __future__ import annotations

from collections.abc import Callable, Hashable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import NotRequired, TypedDict

from constants import DEFAULT_STATIC_RELATION_LABEL
from static_analyzer.node import Node


class EdgeKind(StrEnum):
    """Kind of an edge: a call, a structural relationship a call graph misses, or a wiring fact.

    CALL is what ``CallGraph.edges`` holds and is never the kind of a ``ReferenceEdge``. The
    structural kinds come from the language engines: a method belongs to its class (CONTAINS), a
    class extends another (INHERITS), code names a type (TYPEREF), a module imports another
    (IMPORT). The wiring kinds come from the files that connect units at runtime — manifests,
    deployment topology, configuration — and are specified in ``docs/design/wiring.md``.

    Each kind answers four questions in one place, so every consumer reads the same policy: what
    a relation drawn from it alone is called, whether it is drawn as a component relation at
    all, whether it may pull a file into a component, and whether it is infrastructure a reader
    would rather see de-emphasised.
    """

    CALL = "call"
    CONTAINS = "contains"
    INHERITS = "inherits"
    TYPEREF = "typeref"
    IMPORT = "import"
    DEPENDS_ON = "depends_on"
    CALLS_HTTP = "calls_http"
    ROUTES_TO = "routes_to"
    USES = "uses"
    REGISTERS_WITH = "registers_with"
    FETCHES_CONFIG = "fetches_config"
    REPORTS_TO = "reports_to"

    @property
    def relation_label(self) -> str:
        """The verb a relation gets when edges of this kind alone connect two components."""
        return _SPEC[self].label

    @property
    def drawn(self) -> bool:
        """Whether edges of this kind alone make a component relation.

        Calls always do. Of the reference kinds only the runtime wiring kinds do: CONTAINS and
        INHERITS complete the structure the clustering reads, TYPEREF and IMPORT have no producer,
        and DEPENDS_ON is a build dependency — kept in the wiring results and the dumps for P3, not
        drawn, because on every library of the negative set it is the only kind that joins and no
        maintainer's picture draws a project reference.
        """
        return self is EdgeKind.CALL or _SPEC[self].drawn

    @property
    def affine(self) -> bool:
        """Whether edges of this kind count as links when files are grouped into components."""
        return _SPEC[self].affine

    @property
    def infrastructure(self) -> bool:
        """Whether this is a flow into a registry, a configuration server or telemetry.

        Drawn, but de-emphasised: a star of such arrows says the same thing about every service.
        """
        return _SPEC[self].infrastructure


@dataclass(frozen=True)
class _Spec:
    label: str
    drawn: bool = False
    affine: bool = False
    infrastructure: bool = False


# The policy surface, one row per kind. A wiring kind is drawn and never affine: a communication
# edge says who talks to whom, not what belongs together (with shared-contract references
# affine, eShop's Payment service folded into the bus). Whether a structural kind is affine is
# the clustering's decision; it lives here so that the clustering and the relations agree.
_SPEC: dict[EdgeKind, _Spec] = {
    EdgeKind.CALL: _Spec(DEFAULT_STATIC_RELATION_LABEL),
    EdgeKind.CONTAINS: _Spec("contains"),
    EdgeKind.INHERITS: _Spec("inherits from", affine=True),
    EdgeKind.TYPEREF: _Spec("uses", affine=True),
    EdgeKind.IMPORT: _Spec("imports"),
    EdgeKind.DEPENDS_ON: _Spec("depends on"),
    EdgeKind.CALLS_HTTP: _Spec("calls over HTTP", drawn=True),
    EdgeKind.ROUTES_TO: _Spec("routes to", drawn=True),
    EdgeKind.USES: _Spec("uses", drawn=True),
    EdgeKind.REGISTERS_WITH: _Spec("registers with", drawn=True, infrastructure=True),
    EdgeKind.FETCHES_CONFIG: _Spec("fetches configuration from", drawn=True, infrastructure=True),
    EdgeKind.REPORTS_TO: _Spec("reports to", drawn=True, infrastructure=True),
}


# What structural consumers fold into ``to_networkx`` on top of call edges. The call graph
# leaves ~a fifth of symbols isolated (constructors, dunders, DI/interface methods), so
# completing it with these avoids grab-bag components. TYPEREF and IMPORT are plumbed through
# ``LanguageAnalysisResult`` but no engine emits them yet, and IMPORT is expected to over-merge
# (coarse, dense, file-level) when one does.
DEFAULT_REFERENCE_KINDS: tuple[EdgeKind, ...] = (EdgeKind.CONTAINS, EdgeKind.INHERITS)

# The reference kinds that, like a call, make one component depend on another.
RELATION_REFERENCE_KINDS: frozenset[EdgeKind] = frozenset(
    kind for kind in EdgeKind if kind is not EdgeKind.CALL and kind.drawn
)

# The reference kinds that count as links when files are grouped into components.
AFFINE_REFERENCE_KINDS: frozenset[EdgeKind] = frozenset(kind for kind in EdgeKind if kind.affine)


class CallSiteLocation(TypedDict):
    """A one-based source location where an edge occurs: a call, or a name being written."""

    line: int
    file: NotRequired[str]
    column: NotRequired[int]


def normalize_call_site(call_site: Mapping[str, Hashable]) -> CallSiteLocation:
    file = call_site.get("file", call_site.get("file_path"))
    line = call_site.get("line")
    column = call_site.get("column")
    if not isinstance(line, int) or line < 1:
        raise ValueError("Call sites require a one-based integer line")
    location = CallSiteLocation(line=line)
    if file is not None:
        if not isinstance(file, str):
            raise ValueError("Call-site files must be strings")
        location["file"] = file
    if column is not None:
        if not isinstance(column, int) or column < 1:
            raise ValueError("Call-site columns must be one-based integers")
        location["column"] = column
    return location


@dataclass(frozen=True)
class ReferenceEdge:
    """A non-call relationship between two qualified names, and where the source writes it.

    Two edges are the same edge when they join the same names the same way; the sites are what
    the source wrote and never decide identity, so a graph keeps one edge per (src, dst, kind)
    and the sites it saw first.
    """

    src: str
    dst: str
    kind: EdgeKind
    sites: tuple[CallSiteLocation, ...] = field(default=(), compare=False)

    def __post_init__(self) -> None:
        if self.kind is EdgeKind.CALL:
            raise ValueError("a call is an Edge with call sites, not a ReferenceEdge")
        object.__setattr__(self, "sites", tuple(normalize_call_site(site) for site in self.sites))

    def __setstate__(self, state: dict[str, object]) -> None:
        # A pickle written before edges carried sites has no such attribute; it loads as an
        # edge with no sites rather than failing the first time a consumer reads them.
        self.__dict__.update({"sites": (), **state})

    def visit_paths(self, fn: Callable[[str], str]) -> ReferenceEdge:
        """This edge with every site's file rewritten by ``fn``; itself when no site names one."""
        if not any("file" in site for site in self.sites):
            return self
        sites: list[CallSiteLocation] = []
        for site in self.sites:
            moved = site.copy()
            if "file" in moved:
                moved["file"] = fn(moved["file"])
            sites.append(moved)
        return ReferenceEdge(self.src, self.dst, self.kind, tuple(sites))


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
        call_site = normalize_call_site(call_site)
        call_site_key = tuple(sorted(call_site.items()))
        if call_site_key not in self._call_site_keys:
            self._call_site_keys.add(call_site_key)
            self._call_sites.append(call_site)

    def visit_paths(self, fn: Callable[[str], str]) -> None:
        for site in self._call_sites:
            if "file" in site:
                site["file"] = fn(str(site["file"]))
        self._call_site_keys = {tuple(sorted(site.items())) for site in self._call_sites}
