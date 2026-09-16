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
    IGNORED_MANIFEST = "ignored_manifest"
    UNIT_WITHOUT_MANIFEST = "unit_without_manifest"
    UNREADABLE_MANIFEST = "unreadable_manifest"
    UNRESOLVED_IMAGE = "unresolved_image"


@dataclass(frozen=True)
class Diagnostic:
    code: DiagnosticCode
    message: str
    paths: tuple[str, ...] = ()

    def to_json(self) -> dict:
        return {"code": self.code.value, "message": self.message, "paths": list(self.paths)}


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
    """Where a wiring key is declared or used: the key as written, normalised, and its place."""

    family: AnchorFamily
    role: AnchorRole
    key: str
    norm_key: str
    file: str
    line: int
    column: int = 1
    unit: str = ""
    tier: Tier = Tier.T1


@dataclass(frozen=True)
class ResourceChild:
    """A database on a server, a route on a gateway: grounded substructure of a resource."""

    key: str
    kind: ResourceKind
    name: str
    owner: str = ""


@dataclass(frozen=True)
class Resource:
    key: str
    kind: ResourceKind
    name: str
    declared_by: tuple[str, ...] = ()
    home: str = ""
    children: tuple[ResourceChild, ...] = ()


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
