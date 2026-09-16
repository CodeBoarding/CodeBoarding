"""What the wiring pass produces: the unit table, the anchors, the edges, the resources, the diagnostics.

`docs/design/wiring.md` is the contract. A unit is a directory this repository builds or deploys as
one thing (§2); an anchor is a place where a wiring key is declared or used (§8); a resource is a
thing the code talks to that holds no source here (§7). Every list is sorted, so two runs over one
tree produce one answer.

The types live beside ``language_results`` rather than inside ``static_analyzer.wiring`` because
``StaticAnalysisResults`` carries the bucket and the pass reads the results: one of the two has to
be the leaf, and it is this one.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from static_analyzer.cfg import ReferenceEdge


#: What a resource's identifier starts with, so a relation end, a node name or an anchor's `unit`
#: says which of the two it is without a lookup: `resource:<kind>:<name>`, a child under it as
#: `resource:<kind>:<name>/<kind>:<child>` (§8).
RESOURCE_PREFIX = "resource:"


def is_resource(identifier: str) -> bool:
    return identifier.startswith(RESOURCE_PREFIX)


def resource_kind(identifier: str) -> str:
    """The kind a resource key names, or empty for anything that is not one."""
    return identifier.split(":")[1] if is_resource(identifier) else ""


class UnitKind(StrEnum):
    """What declares a unit. A directory several files declare takes the first kind in this order.

    Why the order: what a directory *builds* says more about it than what runs it, so a compose
    service that builds a Maven module is a Maven module with a service name.
    """

    CSPROJ = "csproj"
    MAVEN = "maven"
    GRADLE = "gradle"
    GO = "go"
    RUST = "rust"
    PYTHON = "python"
    NPM = "npm"
    RUBY = "ruby"
    PHP = "php"
    ELIXIR = "elixir"
    DOCKER = "docker"
    COMPOSE = "compose"
    SKAFFOLD = "skaffold"
    ASPIRE = "aspire"
    K8S = "k8s"
    HELM = "helm"
    OTHER = "other"


class AnchorFamily(StrEnum):
    """Which kind of file a wiring key lives in."""

    BUILD_MANIFEST = "build_manifest"
    DEPLOYMENT = "deployment"
    CONFIGURATION = "configuration"
    SERVICE_NAMES = "service_names"
    HTTP_IN_CODE = "http_in_code"
    MESSAGING = "messaging"
    GENERATED = "generated"
    DATA_ACCESS = "data_access"


class AnchorRole(StrEnum):
    DEF = "def"
    USE = "use"


class Tier(StrEnum):
    """How literal a key is: an exact key, a template, or a guess. Only T1 and T2 make edges."""

    T1 = "T1"
    T2 = "T2"
    T3 = "T3"


class ResourceKind(StrEnum):
    """What a resource is, from the catalogue over image names and well-known keys (§7)."""

    DB = "db"
    CACHE = "cache"
    STORE = "store"
    BROKER = "broker"
    GATEWAY = "gateway"
    API = "api"
    ACTOR = "actor"


class DiagnosticCode(StrEnum):
    """Why the pass could not use something it read. Every one of these is a row, never a log line."""

    AMBIGUOUS_ALIAS = "ambiguous_alias"
    AMBIGUOUS_IMAGE = "ambiguous_image"
    AMBIGUOUS_KEY = "ambiguous_key"
    CONFIGURED_IMAGE = "configured_image"
    IGNORED_MANIFEST = "ignored_manifest"
    NO_BOX_FOR_UNIT = "no_box_for_unit"
    UNIT_WITHOUT_MANIFEST = "unit_without_manifest"
    UNKNOWN_IMAGE_KIND = "unknown_image_kind"
    UNREADABLE_MANIFEST = "unreadable_manifest"
    UNRESOLVED_IMAGE = "unresolved_image"
    UNRESOLVED_USE = "unresolved_use"
    UNUSED_DEFINITION = "unused_definition"
    USE_WITHOUT_UNIT = "use_without_unit"


@dataclass(frozen=True)
class Diagnostic:
    """A row about something the pass read and could not use, with enough structure to act on.

    For an anchor that joined nothing, ``file``, ``line`` and ``key`` say where it is, ``context``
    holds the ten lines around it and ``candidates`` the unit names in play — the shape a resolver
    would read one day, and what a reader needs to check a row by hand today.
    """

    code: DiagnosticCode
    message: str
    paths: tuple[str, ...] = ()
    file: str = ""
    line: int = 0
    key: str = ""
    context: tuple[str, ...] = ()
    candidates: tuple[str, ...] = ()

    def to_json(self) -> dict:
        row: dict = {"code": self.code.value, "message": self.message, "paths": list(self.paths)}
        if self.file:
            row.update({"file": self.file, "line": self.line, "key": self.key, "context": list(self.context)})
        if self.candidates:
            row["candidates"] = list(self.candidates)
        return row


@dataclass(frozen=True)
class Unit:
    """A directory the repository builds or deploys as one thing, and every name it answers to.

    The id is the directory, which is what makes it stable: a unit is one directory, and a
    directory is one unit however many manifests name it.
    """

    id: str
    dir: str
    kind: UnitKind
    manifest: str = ""
    aliases: tuple[str, ...] = ()
    builds: tuple[str, ...] = ()
    variant: tuple[str, ...] = ()

    def to_json(self) -> dict:
        return {
            "id": self.id,
            "dir": self.dir,
            "kind": self.kind.value,
            "manifest": self.manifest,
            "aliases": list(self.aliases),
            "builds": list(self.builds),
            "variant": list(self.variant),
        }


@dataclass(frozen=True)
class Anchor:
    """Where a wiring key is declared or used: the key as written, normalised, and its place.

    ``unit`` is the unit the anchor is about — or, for a file that is a resource's own configuration
    (a Prometheus's scrape list), the resource's key (§6). ``setting`` is the configuration key a
    value was read under (`spring.config.import`, `CONFIG_SERVER_URL`) when the anchor is the host
    that value names, so a join can read what the connection is for from the key that expresses it
    rather than from the host's spelling.
    """

    family: AnchorFamily
    role: AnchorRole
    key: str
    norm_key: str
    file: str
    line: int
    column: int = 1
    unit: str = ""
    tier: Tier = Tier.T1
    setting: str = ""


@dataclass(frozen=True)
class ResourceChild:
    """A database on a server, a route on a gateway: grounded substructure of a resource.

    ``owner`` is the one unit that names it; ``home`` is that unit's component once the nodes are
    placed, empty until then.
    """

    key: str
    kind: ResourceKind
    name: str
    owner: str = ""
    home: str = ""


@dataclass(frozen=True)
class Resource:
    """A thing the code talks to that holds no source here, named as the repository names it (§7).

    ``display_name`` is the catalogue's name for its kind of thing (`mssql/server` -> SQL Server), for
    rendering a node whose declared name would tell a reader nothing. ``users`` are the units whose
    own setting, driver or client names it or one of its children. ``home_unit`` is whose it is when
    the pass runs — a unit or the directory its users share — because a component is the
    clustering's answer and the clustering has not run yet.

    The last four are placement, filled in after the clustering (§7): ``home`` is the component the
    node is drawn in (empty at the top), ``level`` the depth it is drawn at, ``badge`` whether it is
    shown on its owner's box rather than as a node, and ``group`` the node it is folded into over
    the level cap (`Infrastructure`, `External services`, `Data stores`), empty when drawn alone.
    """

    key: str
    kind: ResourceKind
    name: str
    display_name: str = ""
    declared_by: tuple[str, ...] = ()
    users: tuple[str, ...] = ()
    home_unit: str = ""
    children: tuple[ResourceChild, ...] = ()
    home: str = ""
    level: int = 0
    badge: bool = False
    group: str = ""


@dataclass
class WiringResults:
    """The wiring bucket of a static-analysis run.

    Never a ``Language`` key: a compose file wires a Python service to a Java one, so these edges
    belong to no single engine's results.
    """

    units: list[Unit] = field(default_factory=list)
    anchors: list[Anchor] = field(default_factory=list)
    edges: list[ReferenceEdge] = field(default_factory=list)
    resources: list[Resource] = field(default_factory=list)
    diagnostics: list[Diagnostic] = field(default_factory=list)
