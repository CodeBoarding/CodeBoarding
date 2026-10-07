"""What the code talks to that holds no source here: a database, a cache, a broker, a gateway.

The unit table says what this repository builds. A resource is the other end of an arrow that
leaves it — a compose service running a stock image, an Aspire resource no project backs, a server
named only in a connection string, a driver a manifest depends on, a client type in code — so what
makes something a resource is that the repository declares it and builds none of it.

Three questions, in order. **What is it**: one catalogue over the vocabularies a repository uses to
name the same thing, an image (`openzipkin/zipkin`), a constructor (`AddRedis`), a scheme
(`amqp://`), a driver (`Npgsql`) and a client type (`VectorStore`), because a catalogue that decides
the kind has already decided the word a reader sees; an image or a constructor it does not know is
a row. **What does it hold**: a connection string names the databases on a server, and those are
children rather than resources of their own. **Whose is it**: the unit whose declaration defines
its content, else its only user, else the directory its users share — a manifest that merely runs
a resource decides nothing (§7).

Nothing here draws anything. Placement, the level cap and the arrows into these nodes are §7's
remaining half and belong to the PR after this one.
"""

from __future__ import annotations

import os
import re
from collections.abc import Sequence
from dataclasses import dataclass

from static_analyzer.wiring.anchors.keys import Names, Owners
from static_analyzer.wiring.compose import ComposeProject
from static_analyzer.wiring.images import image_ref
from static_analyzer.wiring.manifests import dependencies
from static_analyzer.wiring.scan import FileKind, Scan
from static_analyzer.wiring.topology import ASPIRE_HOST, configures_image
from static_analyzer.wiring.units import alias_key
from static_analyzer.wiring_results import (
    Anchor,
    AnchorFamily,
    AnchorRole,
    Diagnostic,
    DiagnosticCode,
    Resource,
    ResourceChild,
    ResourceKind,
    Unit,
)

#: The one catalogue (§7). A row is reached by an image name, an Aspire constructor, a URL scheme,
#: a driver or a client type, and it carries both the kind and the name a reader sees, so the two
#: can never disagree. Keys are the bare word each vocabulary reduces to; §12 records that this
#: table is its home.
CATALOGUE: dict[str, tuple[ResourceKind, str]] = {
    "postgres": (ResourceKind.DB, "PostgreSQL"),
    "postgresql": (ResourceKind.DB, "PostgreSQL"),
    "postgis": (ResourceKind.DB, "PostgreSQL"),
    "pgvector": (ResourceKind.DB, "PostgreSQL"),
    "timescaledb": (ResourceKind.DB, "TimescaleDB"),
    "mysql": (ResourceKind.DB, "MySQL"),
    "mariadb": (ResourceKind.DB, "MariaDB"),
    "mssql": (ResourceKind.DB, "SQL Server"),
    "sqlserver": (ResourceKind.DB, "SQL Server"),
    "mongo": (ResourceKind.DB, "MongoDB"),
    "mongodb": (ResourceKind.DB, "MongoDB"),
    "cassandra": (ResourceKind.DB, "Cassandra"),
    "scylla": (ResourceKind.DB, "ScyllaDB"),
    "cockroach": (ResourceKind.DB, "CockroachDB"),
    "cockroachdb": (ResourceKind.DB, "CockroachDB"),
    "couchdb": (ResourceKind.DB, "CouchDB"),
    "couchbase": (ResourceKind.DB, "Couchbase"),
    "oracle": (ResourceKind.DB, "Oracle"),
    "hsqldb": (ResourceKind.DB, "HSQLDB"),
    "sqlite": (ResourceKind.DB, "SQLite"),
    "clickhouse": (ResourceKind.DB, "ClickHouse"),
    "neo4j": (ResourceKind.DB, "Neo4j"),
    "cosmos": (ResourceKind.DB, "Azure Cosmos DB"),
    "qdrant": (ResourceKind.DB, "Qdrant"),
    "weaviate": (ResourceKind.DB, "Weaviate"),
    "milvus": (ResourceKind.DB, "Milvus"),
    "chroma": (ResourceKind.DB, "Chroma"),
    "vectorstore": (ResourceKind.DB, "Vector store"),
    "jdbc": (ResourceKind.DB, "SQL database"),
    "redis": (ResourceKind.CACHE, "Redis"),
    "valkey": (ResourceKind.CACHE, "Valkey"),
    "dragonfly": (ResourceKind.CACHE, "Dragonfly"),
    "keydb": (ResourceKind.CACHE, "KeyDB"),
    "memcached": (ResourceKind.CACHE, "Memcached"),
    "connectionmultiplexer": (ResourceKind.CACHE, "Redis"),
    "minio": (ResourceKind.STORE, "MinIO"),
    "s3": (ResourceKind.STORE, "S3"),
    "azurite": (ResourceKind.STORE, "Azure Storage"),
    "blob": (ResourceKind.STORE, "Azure Blob Storage"),
    "fakegcs": (ResourceKind.STORE, "Cloud Storage"),
    "etcd": (ResourceKind.STORE, "etcd"),
    "git": (ResourceKind.STORE, "Git repository"),
    "elasticsearch": (ResourceKind.STORE, "Elasticsearch"),
    "opensearch": (ResourceKind.STORE, "OpenSearch"),
    "rabbitmq": (ResourceKind.BROKER, "RabbitMQ"),
    "rabbit": (ResourceKind.BROKER, "RabbitMQ"),
    "amqp": (ResourceKind.BROKER, "AMQP broker"),
    "kafka": (ResourceKind.BROKER, "Kafka"),
    "redpanda": (ResourceKind.BROKER, "Redpanda"),
    "nats": (ResourceKind.BROKER, "NATS"),
    "pulsar": (ResourceKind.BROKER, "Pulsar"),
    "activemq": (ResourceKind.BROKER, "ActiveMQ"),
    "artemis": (ResourceKind.BROKER, "ActiveMQ Artemis"),
    "zookeeper": (ResourceKind.BROKER, "ZooKeeper"),
    "azureservicebus": (ResourceKind.BROKER, "Azure Service Bus"),
    "servicebus": (ResourceKind.BROKER, "Azure Service Bus"),
    "mosquitto": (ResourceKind.BROKER, "Mosquitto"),
    "nginx": (ResourceKind.GATEWAY, "nginx"),
    "envoy": (ResourceKind.GATEWAY, "Envoy"),
    "traefik": (ResourceKind.GATEWAY, "Traefik"),
    "haproxy": (ResourceKind.GATEWAY, "HAProxy"),
    "kong": (ResourceKind.GATEWAY, "Kong"),
    "caddy": (ResourceKind.GATEWAY, "Caddy"),
    "yarp": (ResourceKind.GATEWAY, "YARP"),
    "zipkin": (ResourceKind.API, "Zipkin"),
    "jaeger": (ResourceKind.API, "Jaeger"),
    "prometheus": (ResourceKind.API, "Prometheus"),
    "grafana": (ResourceKind.API, "Grafana"),
    "opentelemetry": (ResourceKind.API, "OpenTelemetry"),
    "otel": (ResourceKind.API, "OpenTelemetry"),
    "otlp": (ResourceKind.API, "OpenTelemetry"),
    "kibana": (ResourceKind.API, "Kibana"),
    "logstash": (ResourceKind.API, "Logstash"),
    "fluentd": (ResourceKind.API, "Fluentd"),
    "loki": (ResourceKind.API, "Loki"),
    "tempo": (ResourceKind.API, "Tempo"),
    "seq": (ResourceKind.API, "Seq"),
    "temporal": (ResourceKind.API, "Temporal"),
    "keycloak": (ResourceKind.API, "Keycloak"),
    "vault": (ResourceKind.API, "Vault"),
    "ollama": (ResourceKind.API, "Ollama"),
    "openai": (ResourceKind.API, "OpenAI"),
    "azureopenai": (ResourceKind.API, "Azure OpenAI"),
    "foundry": (ResourceKind.API, "Azure AI Foundry"),
    "anthropic": (ResourceKind.API, "Anthropic"),
    "bedrock": (ResourceKind.API, "Bedrock"),
    "sentry": (ResourceKind.API, "Sentry"),
    "sendgrid": (ResourceKind.API, "SendGrid"),
    "twilio": (ResourceKind.API, "Twilio"),
    "stripe": (ResourceKind.API, "Stripe"),
    "mailhog": (ResourceKind.API, "MailHog"),
    "maildev": (ResourceKind.API, "MailDev"),
    "mailpit": (ResourceKind.API, "Mailpit"),
    "localstack": (ResourceKind.API, "LocalStack"),
}

#: The driver a manifest depends on, by its package name in Maven Central, NuGet or npm, and the
#: catalogue word it names: a `pom.xml` that depends on `hsqldb` talks to an HSQLDB.
DRIVERS: dict[str, str] = {
    "hsqldb": "hsqldb",
    "mysql-connector-j": "mysql",
    "mysql-connector-java": "mysql",
    "mariadb-java-client": "mariadb",
    "postgresql": "postgres",
    "npgsql": "postgres",
    "pg": "postgres",
    "mysql2": "mysql",
    "ioredis": "redis",
    "redis": "redis",
    "jedis": "redis",
    "lettuce-core": "redis",
    "stackexchange.redis": "redis",
    "mongodb": "mongodb",
    "mongodb-driver-sync": "mongodb",
    "mongodb.driver": "mongodb",
    "microsoft.data.sqlclient": "sqlserver",
    "system.data.sqlclient": "sqlserver",
    "rabbitmq.client": "rabbitmq",
    "amqp-client": "rabbitmq",
    "amqplib": "rabbitmq",
    "kafka-clients": "kafka",
    "kafkajs": "kafka",
    "confluent.kafka": "kafka",
}

#: `var cache = builder.AddRedis("redis")` — the variable it binds, the constructor that says the
#: kind, and the name the repository gives it. The binding is optional, because
#: `builder.AddYarp("mobile-bff")` declares a gateway without keeping a handle on it.
ASPIRE_RESOURCE = re.compile(r"(?:(?:var|let)\s+(\w+)\s*=\s*)?[\w.]*\bAdd(\w+)\s*\(\s*\"([^\"]+)\"")

#: `var catalogDb = postgres.AddDatabase("catalogdb")` — what a server holds hangs off the variable
#: that server was bound to, so the receiver is the parent and this is never a resource of its own.
ASPIRE_CHILD = re.compile(
    r"(?:var|let)\s+(\w+)\s*=\s*(\w+)\s*\.\s*Add(?:Database|Deployment|Model)\s*\(\s*\"([^\"]+)\""
)

#: The constructors that hang something off another resource rather than declaring one: a database
#: on a server, a model deployment on a provider.
ASPIRE_CHILDREN = frozenset({"database", "deployment", "model"})

#: An Aspire constructor that registers code of this repository, or a value, rather than a thing it talks to.
ASPIRE_PROJECTS = frozenset(
    {"project", "npmapp", "viteapp", "nodeapp", "pythonapp", "pythonmodule", "uvicornapp",
     "javaapp", "golangapp", "dockerfile", "container", "executable", "connectionstring", "parameter"}
)  # fmt: skip

#: `Server=postgres` in a connection string: the machine a store runs on, named by the repository.
SERVER_PART = re.compile(r"(?i)^(?:server|host|data source|datasource)=(.+)$")

#: `Database=CatalogDB` in a connection string: what a server holds, rather than a thing of its own.
DATABASE_PART = re.compile(r"(?i)^(?:database|initial catalog)=(.+)$")

#: A tag or a digest is how an image is pinned, never part of what it is.
_VERSIONED = re.compile(r"(?i)[-_.]?(?:v?\d[\w.]*|alpine|slim|latest|bookworm|bullseye|focal|jammy)$")
_CONNECTION_SETTING = re.compile(r"(?i)connectionstring|datasource|jdbc")
_LONGEST_FIRST = sorted(CATALOGUE, key=len, reverse=True)


@dataclass(frozen=True)
class _Declared:
    """One thing a topology named, before it is known whether the repository builds it."""

    name: str
    kind: ResourceKind
    display_name: str
    file: str
    users: tuple[str, ...] = ()


def discover(
    scan: Scan, projects: list[ComposeProject], units: list[Unit], anchors: list[Anchor]
) -> tuple[list[Resource], list[Diagnostic]]:
    """Every resource this repository declares and builds none of, sorted, with its children; and every image
    or constructor the catalogue did not know."""
    names = Names(units)
    owners = Owners(units)
    aspire, aspire_children, rows = _aspire(scan, owners, names)
    declared, unknown = _compose(scan, projects, names)
    rows += unknown
    declared += aspire
    declared += _stores(anchors)
    # A unit that nothing runs talks to nothing this layer can draw. A library reading
    # `OPENAI_API_KEY`, depending on `pg` or holding a `VectorStore` offers an option to whoever
    # imports it; it does not stand beside a service. A framework's sample app does run.
    running = _deployed_units(scan, projects, units, names)
    declared += _drivers(scan, [unit for unit in units if unit.id in running])
    declared += _clients([anchor for anchor in anchors if anchor.unit in running])
    declared += _configured([anchor for anchor in anchors if anchor.unit in running], names)
    children = _merge(_children(anchors), aspire_children)
    users = _users(anchors, children)
    for held in children.values():
        for child, owner in held.items():
            # A child declared by an AppHost is owned by the one unit that takes a reference to it.
            naming = users.get(alias_key(child), set())
            if not owner and len(naming) == 1:
                held[child] = next(iter(naming))
    for entry in declared:
        # The unit whose setting, driver or client names a thing uses it; a compose file or an
        # AppHost that merely runs it decides nothing (§7), so those declare with no user.
        users.setdefault(alias_key(entry.name), set()).update(entry.users)
    schemas = _schemas(anchors)

    found: dict[str, Resource] = {}
    for entry in declared:
        key = f"resource:{entry.kind.value}:{alias_key(entry.name)}"
        existing = found.get(key)
        declared_by = tuple(sorted({*(existing.declared_by if existing else ()), entry.file}))
        using = sorted(users.get(alias_key(entry.name), set()))
        found[key] = Resource(
            key=key,
            kind=entry.kind,
            # What the repository calls it, never the image (§4).
            name=entry.name,
            display_name=entry.display_name,
            declared_by=declared_by,
            home_unit=_home(using, schemas),
            children=tuple(
                # A child is of its parent's kind: a database on a server, a model on a provider.
                ResourceChild(
                    key=f"{key}/{entry.kind.value}:{alias_key(child)}",
                    kind=entry.kind,
                    name=child,
                    owner=owner,
                )
                for child, owner in sorted(children.get(alias_key(entry.name), {}).items())
            ),
        )
    return [found[key] for key in sorted(found)], rows


def classify(token: str) -> tuple[ResourceKind | None, str]:
    """What a word says a thing is, and what to call it: the word itself, or the longest catalogue word inside it.

    `bitnami/postgresql-repmgr` and `SimpleVectorStore` carry their word; `openai-key` and
    `vaultwarden` carry one too, which is why a compose service name and a client type are read
    this way and an Aspire constructor is not (`classify_word`).
    """
    word = _reduce(token)
    if word in CATALOGUE:
        return CATALOGUE[word]
    for known in _LONGEST_FIRST:
        if known in word:
            return CATALOGUE[known]
    return None, ""


def classify_word(token: str) -> tuple[ResourceKind | None, str]:
    """What a word says when it must be the whole word: an Aspire constructor, a configuration key segment."""
    return CATALOGUE.get(_reduce(token), (None, ""))


def classify_image(repository: str) -> tuple[ResourceKind | None, str]:
    """What an image is, from any of its path segments, the last first: `mssql/server` is a SQL Server."""
    for segment in reversed(repository.split("/")):
        kind, display = classify(segment)
        if kind is not None:
            return kind, display
    return None, ""


def _reduce(token: str) -> str:
    """A word from an image, a constructor or a scheme, reduced to what the catalogue is keyed on."""
    word = token.strip().lower().rsplit("/", 1)[-1]
    word = _VERSIONED.sub("", word)
    return re.sub(r"[^a-z0-9]", "", word)


def _compose(scan: Scan, projects: list[ComposeProject], names: Names) -> tuple[list[_Declared], list[Diagnostic]]:
    """A compose service running an image nothing here builds is a thing this repository talks to.

    The image decides the kind, and failing that the service's own name does, because a service
    called `postgres` is a PostgreSQL; a service running an image the catalogue does not know is
    a row rather than a silence. A build that copies only configuration onto a stock image is that
    image (§6).
    """
    found: list[_Declared] = []
    rows: list[Diagnostic] = []
    built = {
        image_ref(service.image).repository
        for project in projects
        for service in project.services
        if service.image and (service.context or service.dockerfile)
    }
    for project in projects:
        for service in project.services:
            if names.unit_of(service.name):
                continue
            configured = configures_image(scan, service)
            if configured is not None:
                image, file = configured.base_image, configured.path
            elif service.context or service.dockerfile or not service.image:
                # A build of this repository is a unit or a toolchain image, never a resource.
                continue
            else:
                image, file = service.image, service.files[0]
            repository = image_ref(image).repository
            if not repository or (configured is None and repository in built):
                # An image this repository builds is a unit, or an ambiguity already reported.
                continue
            kind, display = classify_image(repository)
            if kind is None:
                kind, display = classify(service.name)
            if kind is None:
                rows.append(
                    Diagnostic(
                        code=DiagnosticCode.UNKNOWN_IMAGE_KIND,
                        message=f"{service.name} in {file} runs {image}, which the catalogue does not know",
                        paths=(file,),
                        file=file,
                        line=service.line,
                        key=image,
                        candidates=(service.name,),
                    )
                )
                continue
            found.append(_Declared(name=service.name, kind=kind, display_name=display, file=file))
    return found, rows


def _aspire(
    scan: Scan, owners: Owners, names: Names
) -> tuple[list[_Declared], dict[str, dict[str, str]], list[Diagnostic]]:
    """An AppHost registers what it runs; a known constructor says which of those are not code here.

    Why the project first: an AppHost is a manifest written in C#, and its own `.cs` files are the
    one source the allowlist opens by name (§3), so they are reached through the project that
    declares itself a host rather than by walking the tree for C#.

    Why the unit table still wins: a resource is a thing that holds no source here (§2), so where a
    unit of this repository answers to the name — an `AddRabbitMQ("eventbus")` beside an event-bus
    library of the same name — the box is the code, and the container it runs in is how that box is
    deployed rather than a second thing to draw. Why the constructor and never the name: the
    constructor is the declaration, and a name that merely contains a catalogue word
    (`openai-key`, `vaultwarden`) declares nothing.
    """
    found: list[_Declared] = []
    children: dict[str, dict[str, str]] = {}
    rows: list[Diagnostic] = []
    for path in scan.paths_of(FileKind.DOTNET_PROJECT):
        if not any(marker in scan.text(path) for marker in ASPIRE_HOST):
            continue
        inside = owners.of(path)
        for source in scan.sources(inside if inside != "." else ""):
            text = scan.text(source)
            bound = {variable: name for variable, _, name in ASPIRE_RESOURCE.findall(text)}
            for _, parent, child in ASPIRE_CHILD.findall(text):
                if parent in bound:
                    children.setdefault(alias_key(bound[parent]), {})[child] = ""
            for match in ASPIRE_RESOURCE.finditer(text):
                _, constructor, name = match.groups()
                if constructor.lower() in ASPIRE_PROJECTS | ASPIRE_CHILDREN or names.unit_of(name):
                    continue
                kind, display = classify_word(constructor)
                if kind is None:
                    rows.append(
                        Diagnostic(
                            code=DiagnosticCode.UNKNOWN_IMAGE_KIND,
                            message=f"{name} in {source} is registered with Add{constructor}, "
                            "which the catalogue does not know",
                            paths=(source,),
                            file=source,
                            line=text.count("\n", 0, match.start()) + 1,
                            key=f"Add{constructor}",
                            candidates=(name,),
                        )
                    )
                    continue
                found.append(_Declared(name=name, kind=kind, display_name=display, file=source))
    return found, children, rows


def _stores(anchors: list[Anchor]) -> list[_Declared]:
    """A store named only in a connection string: its scheme, its own name or its setting says what it is."""
    hosts = {
        (anchor.file, anchor.line): anchor.key
        for anchor in anchors
        if anchor.family is AnchorFamily.SERVICE_NAMES and anchor.role is AnchorRole.USE
    }
    found = []
    for anchor in anchors:
        if anchor.family is not AnchorFamily.DATA_ACCESS or anchor.role is not AnchorRole.USE:
            continue
        users = (anchor.unit,) if anchor.unit and anchor.unit != "." else ()
        server = SERVER_PART.match(anchor.key)
        if server is not None:
            kind, display = classify(server.group(1))
            if kind is None and _CONNECTION_SETTING.search(anchor.setting):
                # A `Server=` inside a connection string is a database server whatever it is called.
                kind, display = ResourceKind.DB, "SQL database"
            if kind is not None:
                found.append(_Declared(server.group(1), kind, display, anchor.file, users))
        elif ": " in anchor.key:
            # `ConnectionStrings.EventBus: amqp` — the scheme is the kind, and the host written on
            # the same line is the name the repository gave it.
            host = hosts.get((anchor.file, anchor.line), "")
            kind, display = classify(host) if host else (None, "")
            if host and kind is None:
                kind, display = classify_word(anchor.key.rsplit(": ", 1)[1].split(":")[0])
            if host and kind is not None:
                found.append(_Declared(host, kind, display, anchor.file, users))
    return found


def _drivers(scan: Scan, units: list[Unit]) -> list[_Declared]:
    """A driver a manifest depends on declares a resource of its kind, used by that unit (§7)."""
    found = []
    for unit in units:
        if not unit.manifest:
            continue
        for dependency in dependencies(scan, unit.manifest):
            word = DRIVERS.get(dependency.lower())
            if word is None:
                continue
            kind, display = CATALOGUE[word]
            found.append(_Declared(name=word, kind=kind, display_name=display, file=unit.manifest, users=(unit.id,)))
    return found


def _clients(anchors: list[Anchor]) -> list[_Declared]:
    """A vector-store client type in code declares a db: nothing else names it (§7)."""
    found = []
    for anchor in anchors:
        if anchor.family is not AnchorFamily.DATA_ACCESS or anchor.role is not AnchorRole.USE or "=" in anchor.key:
            continue
        if ": " in anchor.key or "vectorstore" not in _reduce(anchor.key):
            continue
        kind, display = CATALOGUE["vectorstore"]
        users = (anchor.unit,) if anchor.unit and anchor.unit != "." else ()
        found.append(_Declared(name="vectorstore", kind=kind, display_name=display, file=anchor.file, users=users))
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
            kind, display = CATALOGUE[_reduce(known)]
            users = (anchor.unit,) if anchor.unit and anchor.unit != "." else ()
            found.append(_Declared(name=known, kind=kind, display_name=display, file=anchor.file, users=users))
    return found


def _deployed_units(scan: Scan, projects: list[ComposeProject], units: list[Unit], names: Names) -> set[str]:
    """The units something here runs: a compose service, an Aspire project, a Kubernetes workload, a
    skaffold artifact, a chart. A compose project running only stock images, or only images it
    configures, runs no unit and is a tool beside the code rather than the system."""
    running = {names.unit_of(service.name) for project in projects for service in project.services}
    deploying = (FileKind.COMPOSE, FileKind.YAML, FileKind.SKAFFOLD, FileKind.HELM_CHART, FileKind.DOTNET_PROJECT)
    for unit in units:
        for built in unit.builds:
            if built.endswith(".cs") or (built in scan.files and scan.files[built].kind in deploying):
                running.add(unit.id)
    return {unit for unit in running if unit}


def _merge(first: dict[str, dict[str, str]], second: dict[str, dict[str, str]]) -> dict[str, dict[str, str]]:
    merged = {parent: dict(held) for parent, held in first.items()}
    for parent, held in second.items():
        merged.setdefault(parent, {}).update({child: owner for child, owner in held.items()})
    return merged


def _children(anchors: list[Anchor]) -> dict[str, dict[str, str]]:
    """The databases a connection string names, under the server it named on the same line, each with
    the unit whose connection string names it as its owner when exactly one does."""
    servers: dict[tuple[str, int], str] = {}
    databases: dict[tuple[str, int], list[tuple[str, str]]] = {}
    for anchor in anchors:
        if anchor.family is not AnchorFamily.DATA_ACCESS or anchor.role is not AnchorRole.USE:
            continue
        place = (anchor.file, anchor.line)
        database = DATABASE_PART.match(anchor.key)
        if database is not None:
            databases.setdefault(place, []).append((database.group(1), anchor.unit))
        elif SERVER_PART.match(anchor.key):
            servers[place] = anchor.key.split("=", 1)[1]
    naming: dict[str, dict[str, set[str]]] = {}
    for place, named in databases.items():
        server = servers.get(place)
        if server:
            for name, unit in named:
                naming.setdefault(alias_key(server), {}).setdefault(name, set()).update({unit} if unit else set())
    return {
        server: {name: (next(iter(units)) if len(units) == 1 else "") for name, units in held.items()}
        for server, held in naming.items()
    }


def _users(anchors: list[Anchor], children: dict[str, dict[str, str]]) -> dict[str, set[str]]:
    """Which units name each thing, a child's users counted for its parent: what makes a resource shared or private."""
    parent_of = {alias_key(child): parent for parent, held in children.items() for child in held}
    found: dict[str, set[str]] = {}
    for anchor in anchors:
        if anchor.role is not AnchorRole.USE or not anchor.unit or anchor.unit == ".":
            continue
        name = alias_key(anchor.key) if anchor.family is AnchorFamily.SERVICE_NAMES else anchor.norm_key
        found.setdefault(name, set()).add(anchor.unit)
        if name in parent_of:
            found.setdefault(parent_of[name], set()).add(anchor.unit)
    return found


def _schemas(anchors: list[Anchor]) -> set[str]:
    """The units that keep a migration or DDL directory, which is a declaration of content."""
    return {
        anchor.unit
        for anchor in anchors
        if anchor.family is AnchorFamily.DATA_ACCESS and anchor.role is AnchorRole.DEF and anchor.unit
    }


def _home(users: Sequence[str], schemas: set[str]) -> str:
    """Whose resource it is: the unit whose declaration defines its content, else its only user, else
    the directory its users share (§7). A unit in P1; PR 6 resolves it to a component."""
    declaring = sorted(set(users) & schemas)
    if len(declaring) == 1:
        return declaring[0]
    if len(users) == 1:
        return users[0]
    if not users:
        return ""
    common = os.path.commonpath([unit if unit != "." else "" for unit in users]) if len(set(users)) > 1 else users[0]
    return common.replace(os.sep, "/") or "."
