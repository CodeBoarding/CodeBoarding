"""What the code talks to that holds no source here: a database, a cache, a broker, a gateway.

The unit table says what this repository builds. A resource is the other end of an arrow that
leaves it — a compose service running a stock image, an Aspire resource no project backs, a server
named only in a connection string — so what makes something a resource is that the repository
declares it and builds none of it.

Three questions, in order. **What is it**: one catalogue over the three vocabularies a repository
uses to name the same thing, an image (`openzipkin/zipkin`), a constructor (`AddRedis`) and a
scheme (`amqp://`), because a catalogue that decides the kind has already decided the word a reader
sees. **What does it hold**: a connection string names the databases on a server, and those are
children rather than resources of their own. **Whose is it**: the unit whose declaration defines its
content, else its only user — a manifest that merely runs a resource decides nothing (§7).

Nothing here draws anything. Placement, the level cap and the arrows into these nodes are §7's
remaining half and belong to the PR after this one.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from static_analyzer.wiring.anchors.keys import Names, Owners
from static_analyzer.wiring.compose import ComposeProject
from static_analyzer.wiring.images import image_ref
from static_analyzer.wiring.scan import FileKind, Scan
from static_analyzer.wiring.units import alias_key
from static_analyzer.wiring_results import (
    Anchor,
    AnchorFamily,
    AnchorRole,
    Resource,
    ResourceChild,
    ResourceKind,
    Unit,
)

#: The one catalogue (§7). A row is reached by an image name, an Aspire constructor or a URL
#: scheme, and it carries both the kind and the name a reader sees, so the two can never disagree.
#: Keys are the bare word each vocabulary reduces to; §12 records that this table is its home.
CATALOGUE: dict[str, tuple[ResourceKind, str]] = {
    "postgres": (ResourceKind.DB, "PostgreSQL"),
    "postgresql": (ResourceKind.DB, "PostgreSQL"),
    "timescaledb": (ResourceKind.DB, "TimescaleDB"),
    "mysql": (ResourceKind.DB, "MySQL"),
    "mariadb": (ResourceKind.DB, "MariaDB"),
    "mssql": (ResourceKind.DB, "SQL Server"),
    "sqlserver": (ResourceKind.DB, "SQL Server"),
    "mongo": (ResourceKind.DB, "MongoDB"),
    "mongodb": (ResourceKind.DB, "MongoDB"),
    "cassandra": (ResourceKind.DB, "Cassandra"),
    "cockroachdb": (ResourceKind.DB, "CockroachDB"),
    "oracle": (ResourceKind.DB, "Oracle"),
    "hsqldb": (ResourceKind.DB, "HSQLDB"),
    "sqlite": (ResourceKind.DB, "SQLite"),
    "clickhouse": (ResourceKind.DB, "ClickHouse"),
    "neo4j": (ResourceKind.DB, "Neo4j"),
    "redis": (ResourceKind.CACHE, "Redis"),
    "valkey": (ResourceKind.CACHE, "Valkey"),
    "memcached": (ResourceKind.CACHE, "Memcached"),
    "minio": (ResourceKind.STORE, "MinIO"),
    "s3": (ResourceKind.STORE, "S3"),
    "azurite": (ResourceKind.STORE, "Azure Storage"),
    "elasticsearch": (ResourceKind.STORE, "Elasticsearch"),
    "opensearch": (ResourceKind.STORE, "OpenSearch"),
    "rabbitmq": (ResourceKind.BROKER, "RabbitMQ"),
    "kafka": (ResourceKind.BROKER, "Kafka"),
    "redpanda": (ResourceKind.BROKER, "Redpanda"),
    "nats": (ResourceKind.BROKER, "NATS"),
    "pulsar": (ResourceKind.BROKER, "Pulsar"),
    "activemq": (ResourceKind.BROKER, "ActiveMQ"),
    "azureservicebus": (ResourceKind.BROKER, "Azure Service Bus"),
    "mosquitto": (ResourceKind.BROKER, "Mosquitto"),
    "nginx": (ResourceKind.GATEWAY, "nginx"),
    "envoy": (ResourceKind.GATEWAY, "Envoy"),
    "traefik": (ResourceKind.GATEWAY, "Traefik"),
    "haproxy": (ResourceKind.GATEWAY, "HAProxy"),
    "kong": (ResourceKind.GATEWAY, "Kong"),
    "yarp": (ResourceKind.GATEWAY, "YARP"),
    "zipkin": (ResourceKind.API, "Zipkin"),
    "jaeger": (ResourceKind.API, "Jaeger"),
    "prometheus": (ResourceKind.API, "Prometheus"),
    "grafana": (ResourceKind.API, "Grafana"),
    "opentelemetry": (ResourceKind.API, "OpenTelemetry"),
    "keycloak": (ResourceKind.API, "Keycloak"),
    "vault": (ResourceKind.API, "Vault"),
    "ollama": (ResourceKind.API, "Ollama"),
    "openai": (ResourceKind.API, "OpenAI"),
    "azureopenai": (ResourceKind.API, "Azure OpenAI"),
    "foundry": (ResourceKind.API, "Azure AI Foundry"),
    "anthropic": (ResourceKind.API, "Anthropic"),
    "bedrock": (ResourceKind.API, "Bedrock"),
    "otlp": (ResourceKind.API, "OpenTelemetry"),
    "sentry": (ResourceKind.API, "Sentry"),
    "sendgrid": (ResourceKind.API, "SendGrid"),
    "twilio": (ResourceKind.API, "Twilio"),
    "stripe": (ResourceKind.API, "Stripe"),
    "mailhog": (ResourceKind.API, "MailHog"),
    "localstack": (ResourceKind.API, "LocalStack"),
}

#: `var cache = builder.AddRedis("redis")` — the variable it binds, the constructor that says the
#: kind, and the name the repository gives it.
#: The binding is optional, because `builder.AddYarp("mobile-bff")` declares a gateway without
#: keeping a handle on it.
ASPIRE_RESOURCE = re.compile(r"(?:(?:var|let)\s+(\w+)\s*=\s*)?[\w.]*\bAdd(\w+)\s*\(\s*\"([^\"]+)\"")

#: `var catalogDb = postgres.AddDatabase("catalogdb")` — what a server holds hangs off the variable
#: that server was bound to, so the receiver is the parent and this is never a resource of its own.
ASPIRE_CHILD = re.compile(r"(?:var|let)\s+\w+\s*=\s*(\w+)\s*\.\s*Add(?:Database|Deployment)\s*\(\s*\"([^\"]+)\"")

#: The constructors that hang something off another resource rather than declaring one: a database
#: on a server, a model deployment on a provider.
ASPIRE_CHILDREN = frozenset({"database", "deployment"})

#: `Server=postgres` in a connection string: the machine a store runs on, named by the repository.
SERVER_PART = re.compile(r"(?i)^(?:server|host|data source|datasource)=(.+)$")

#: What a project says when it is an Aspire AppHost rather than an ordinary .NET project.
ASPIRE_HOST = ("Aspire.AppHost.Sdk", "Aspire.Hosting.AppHost", "<IsAspireHost>true")

#: An Aspire constructor that registers code of this repository rather than a thing it talks to.
ASPIRE_PROJECTS = frozenset(
    {"project", "npmapp", "viteapp", "nodeapp", "pythonapp", "pythonmodule", "uvicornapp",
     "javaapp", "golangapp", "dockerfile", "container", "executable"}
)  # fmt: skip

#: `Database=CatalogDB` in a connection string: what a server holds, rather than a thing of its own.
DATABASE_PART = re.compile(r"(?i)^(?:database|initial catalog)=(.+)$")

#: A tag or a digest is how an image is pinned, never part of what it is.
_VERSIONED = re.compile(r"(?i)[-_.]?(?:v?\d[\w.]*|alpine|slim|latest|bookworm|bullseye|focal|jammy)$")


@dataclass(frozen=True)
class _Declared:
    """One thing a topology named, before it is known whether the repository builds it."""

    name: str
    token: str
    file: str


def discover(scan: Scan, projects: list[ComposeProject], units: list[Unit], anchors: list[Anchor]) -> list[Resource]:
    """Every resource this repository declares and builds none of, sorted, with its children."""
    names = Names(units)
    owners = Owners(units)
    aspire, aspire_children = _aspire(scan, owners, names)
    # A repository that deploys nothing talks to nothing this layer can draw. A library reading
    # `OPENAI_API_KEY` offers an option to whoever imports it; it does not stand beside a service.
    # Without this, every library in the negative set acquired a node — this repository included.
    deployed = bool(projects) or bool(aspire)
    naming = [*_stores(anchors), *(_configured(anchors, names) if deployed else [])]
    declared = [*_compose(projects, names), *aspire, *naming]
    children = _merge(_children(anchors), aspire_children)
    users = _users(anchors)
    for entry in naming:
        # The unit that names a thing uses it; a manifest that merely runs it decides nothing (§7).
        # Why not the anchor's own key: a configuration key is normalised for relaxed binding and a
        # name for the unit table, and the two spellings can never meet.
        unit = owners.of(entry.file)
        if unit and unit != ".":
            users.setdefault(alias_key(entry.name), set()).add(unit)
    schemas = _schemas(anchors)

    found: dict[str, Resource] = {}
    for entry in declared:
        # What it is comes from the word that says so — an image, a constructor, a scheme — and
        # failing that from the name itself, because a service called `postgres` is a PostgreSQL.
        kind, _ = classify(entry.token)
        if kind is None:
            kind, _ = classify(entry.name)
        if kind is None:
            continue
        key = f"resource:{kind.value}:{alias_key(entry.name)}"
        existing = found.get(key)
        declared_by = tuple(sorted({*(existing.declared_by if existing else ()), entry.file}))
        found[key] = Resource(
            key=key,
            kind=kind,
            # What the repository calls it, never the image (§4).
            name=entry.name,
            declared_by=declared_by,
            home=_home(entry.name, users, schemas),
            children=tuple(
                # A child is of its parent's kind: a database on a server, a model on a provider.
                ResourceChild(
                    key=f"{key}/{kind.value}:{alias_key(child)}",
                    kind=kind,
                    name=child,
                    owner=owner,
                )
                for child, owner in sorted(children.get(alias_key(entry.name), {}).items())
            ),
        )
    return [found[key] for key in sorted(found)]


def classify(token: str) -> tuple[ResourceKind | None, str]:
    """What a word from any of the three vocabularies says a thing is, and what to call it."""
    word = _reduce(token)
    if word in CATALOGUE:
        return CATALOGUE[word]
    # A name that carries a known word carries its kind: `bitnami/postgresql-repmgr`, `AddSqlServer`.
    for known in sorted(CATALOGUE, key=len, reverse=True):
        if known in word:
            return CATALOGUE[known]
    return None, ""


def _reduce(token: str) -> str:
    """A word from an image, a constructor or a scheme, reduced to what the catalogue is keyed on."""
    word = token.strip().lower().rsplit("/", 1)[-1]
    word = _VERSIONED.sub("", word)
    return re.sub(r"[^a-z0-9]", "", word)


def _compose(projects: list[ComposeProject], names: Names) -> list[_Declared]:
    """A compose service running an image nothing here builds is a thing this repository talks to."""
    found = []
    for project in projects:
        for service in project.services:
            if service.context or names.unit_of(service.name) or not service.image:
                continue
            repository = image_ref(service.image).repository
            if repository:
                found.append(_Declared(name=service.name, token=repository, file=service.files[0]))
    return found


def _aspire(scan: Scan, owners: Owners, names: Names) -> tuple[list[_Declared], dict[str, dict[str, str]]]:
    """An AppHost registers what it runs; the constructor says which of those are not code here.

    Why the project first: an AppHost is a manifest written in C#, and its own `.cs` files are the
    one source the allowlist opens by name (§3), so they are reached through the project that
    declares itself a host rather than by walking the tree for C#.

    Why the unit table still wins: a resource is a thing that holds no source here (§2), so where a
    unit of this repository answers to the name — an `AddRabbitMQ("eventbus")` beside an event-bus
    library of the same name — the box is the code, and the container it runs in is how that box is
    deployed rather than a second thing to draw.
    """
    found: list[_Declared] = []
    children: dict[str, dict[str, str]] = {}
    for path in scan.paths_of(FileKind.DOTNET_PROJECT):
        if not any(marker in scan.text(path) for marker in ASPIRE_HOST):
            continue
        inside = owners.of(path)
        for source in scan.sources(inside if inside != "." else ""):
            text = scan.text(source)
            bound = {variable: name for variable, _, name in ASPIRE_RESOURCE.findall(text)}
            for variable, child in ASPIRE_CHILD.findall(text):
                if variable in bound:
                    children.setdefault(alias_key(bound[variable]), {})[child] = ""
            for _, constructor, name in ASPIRE_RESOURCE.findall(text):
                if constructor.lower() in ASPIRE_PROJECTS | ASPIRE_CHILDREN or names.unit_of(name):
                    continue
                found.append(_Declared(name=name, token=constructor, file=source))
    return found, children


def _stores(anchors: list[Anchor]) -> list[_Declared]:
    """A store named only in a connection string: its scheme or its own name says what it is."""
    hosts = {
        (anchor.file, anchor.line): anchor.key
        for anchor in anchors
        if anchor.family is AnchorFamily.SERVICE_NAMES and anchor.role is AnchorRole.USE
    }
    found = []
    for anchor in anchors:
        if anchor.family is not AnchorFamily.DATA_ACCESS or anchor.role is not AnchorRole.USE:
            continue
        server = SERVER_PART.match(anchor.key)
        if server is not None:
            found.append(_Declared(name=server.group(1), token=server.group(1), file=anchor.file))
        elif ": " in anchor.key:
            # `ConnectionStrings.EventBus: amqp` — the scheme is the kind, and the host written on
            # the same line is the name the repository gave it.
            host = hosts.get((anchor.file, anchor.line), "")
            if host:
                found.append(_Declared(name=host, token=anchor.key.rsplit(": ", 1)[1], file=anchor.file))
    return found


def _configured(anchors: list[Anchor], names: Names) -> list[_Declared]:
    """A third party a key names: `spring.ai.openai.api-key`, `OTEL_EXPORTER_OTLP_ENDPOINT` (§7).

    The name is the segment that named it, never the whole key: `spring.ai.openai.api-key` and
    `OPENAI_API_KEY` are two keys about one thing, and a key is a way of reaching a resource rather
    than a second resource to draw. The first known segment wins, because `spring.ai.azure.openai`
    names an OpenAI rather than an Azure.
    """
    found = []
    for anchor in anchors:
        if anchor.family is not AnchorFamily.CONFIGURATION:
            continue
        segments = [segment for segment in re.split(r"[.:_\-\[\]]+", anchor.key) if segment]
        known = next((segment for segment in segments if _reduce(segment) in CATALOGUE), "")
        if known and not names.unit_of(known):
            found.append(_Declared(name=known, token=known, file=anchor.file))
    return found


def _merge(first: dict[str, dict[str, str]], second: dict[str, dict[str, str]]) -> dict[str, dict[str, str]]:
    merged = {parent: dict(held) for parent, held in first.items()}
    for parent, held in second.items():
        merged.setdefault(parent, {}).update(held)
    return merged


def _children(anchors: list[Anchor]) -> dict[str, dict[str, str]]:
    """The databases a connection string names, under the server it named on the same line."""
    servers: dict[tuple[str, int], str] = {}
    databases: dict[tuple[str, int], list[str]] = {}
    for anchor in anchors:
        if anchor.family is not AnchorFamily.DATA_ACCESS or anchor.role is not AnchorRole.USE:
            continue
        place = (anchor.file, anchor.line)
        database = DATABASE_PART.match(anchor.key)
        if database is not None:
            databases.setdefault(place, []).append(database.group(1))
        elif anchor.key.lower().startswith(("server=", "host=", "data source=", "datasource=")):
            servers[place] = anchor.key.split("=", 1)[1]
    found: dict[str, dict[str, str]] = {}
    for place, named in databases.items():
        server = servers.get(place)
        if server:
            found.setdefault(alias_key(server), {}).update({name: "" for name in named})
    return found


def _users(anchors: list[Anchor]) -> dict[str, set[str]]:
    """Which units name each thing, which is what makes a resource shared or private."""
    found: dict[str, set[str]] = {}
    for anchor in anchors:
        if anchor.role is AnchorRole.USE and anchor.unit:
            found.setdefault(anchor.norm_key, set()).add(anchor.unit)
    return found


def _schemas(anchors: list[Anchor]) -> set[str]:
    """The units that keep a migration or DDL directory, which is a declaration of content."""
    return {
        anchor.unit
        for anchor in anchors
        if anchor.family is AnchorFamily.DATA_ACCESS and anchor.role is AnchorRole.DEF and anchor.unit
    }


def _home(name: str, users: dict[str, set[str]], schemas: set[str]) -> str:
    """Whose resource it is: the unit whose declaration defines its content, else its only user.

    In P1 this is a unit, because a component is the clustering's answer and the clustering has not
    run yet; the PR that places these nodes resolves it to a component id (§7).
    """
    naming = users.get(alias_key(name), set())
    declaring = sorted(naming & schemas)
    if len(declaring) == 1:
        return declaring[0]
    return next(iter(naming)) if len(naming) == 1 else ""
