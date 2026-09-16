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

Every place a unit names one of these is a *use*, and the uses are what the join turns into arrows
(§6): the resource is the other end. A driver or a client type names a kind of thing; when the
deployment runs exactly one thing of that kind, that is the thing the driver talks to, so the two
declarations are one resource under the name the repository gave it. Nothing here draws anything:
placement and the level cap are the wiring graph's (§7).
"""

from __future__ import annotations

import os
import re
from collections.abc import Sequence
from dataclasses import dataclass

from static_analyzer.wiring.anchors.keys import Names, Owners
from static_analyzer.wiring.catalogue import (
    CATALOGUE,
    DRIVERS,
    classify,
    classify_image,
    classify_word,
    resource_key,
    word_of,
)
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
    is_resource,
)

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
_CONNECTION_SETTING = re.compile(r"(?i)connectionstring|datasource|jdbc")


@dataclass(frozen=True)
class Use:
    """One place where a unit — or a resource's own configuration — names a resource or a child of one.

    ``source`` is the unit it is about, or the resource whose configuration it is; ``target`` the
    resource or child key. The join decides the kind from ``setting`` and ``key`` (§6).
    """

    source: str
    target: str
    file: str
    line: int
    column: int
    key: str
    setting: str
    family: AnchorFamily


@dataclass(frozen=True)
class _Declared:
    """One thing a topology named, before it is known whether the repository builds it.

    ``named`` says whether the repository spelled the name (a compose service, an Aspire resource, the
    host of a connection string) or the catalogue did (a driver, a client type, a configured key),
    which decides which of two declarations of one thing keeps its name.
    """

    name: str
    kind: ResourceKind
    display_name: str
    file: str
    line: int = 0
    named: bool = True


@dataclass(frozen=True)
class _Naming:
    """A place naming something by a name: what the uses are before the thing named has a key."""

    unit: str
    name: str
    file: str
    line: int
    column: int
    key: str
    setting: str
    family: AnchorFamily


def discover(
    scan: Scan, projects: list[ComposeProject], units: list[Unit], anchors: list[Anchor]
) -> tuple[list[Resource], list[Use], list[Diagnostic]]:
    """Every resource this repository declares and builds none of, sorted, with its children and its
    users; every use of one; and every image or constructor the catalogue did not know."""
    names = Names(units)
    owners = Owners(units)
    aspire, aspire_children, rows = _aspire(scan, owners, names)
    declared, unknown = _compose(scan, projects, names)
    rows += unknown
    declared += aspire
    stores, namings = _stores(anchors)
    declared += stores
    # A unit that nothing runs talks to nothing this layer can draw. A library reading
    # `OPENAI_API_KEY`, depending on `pg` or holding a `VectorStore` offers an option to whoever
    # imports it; it does not stand beside a service. A framework's sample app does run.
    running = _deployed_units(scan, projects, units, names)
    for more, named_by in (
        _drivers(scan, [unit for unit in units if unit.id in running]),
        _clients([anchor for anchor in anchors if anchor.unit in running]),
        _configured([anchor for anchor in anchors if anchor.unit in running], names),
    ):
        declared += more
        namings += named_by
    canonical = _canonical(declared)
    children = _merge(_children(anchors), aspire_children)
    parent_of = {alias_key(child): parent for parent, held in children.items() for child in held}
    namings += _named(anchors, canonical, parent_of)

    # A unit uses a resource when its own setting, driver or client names it or names one of its
    # children; a compose file or an AppHost that merely runs it decides nothing (§7), and a
    # resource's own configuration naming another is an arrow, never a user.
    users: dict[str, set[str]] = {}
    for naming in namings:
        parent = canonical.get(alias_key(naming.name)) or canonical.get(parent_of.get(alias_key(naming.name), ""))
        if parent and naming.unit and naming.unit != "." and not is_resource(naming.unit):
            users.setdefault(parent, set()).add(naming.unit)
    for held in children.values():
        for child, owner in held.items():
            # A child declared by an AppHost is owned by the one unit that takes a reference to it.
            naming_units = {
                n.unit for n in namings if alias_key(n.name) == alias_key(child) and n.unit and n.unit != "."
            }
            if not owner and len(naming_units) == 1:
                held[child] = next(iter(naming_units))
    found: dict[str, Resource] = {}
    for entry in sorted(declared, key=lambda entry: (entry.kind.value, entry.name, entry.file, entry.line)):
        name = canonical[alias_key(entry.name)]
        key = resource_key(entry.kind, name)
        existing = found.get(key)
        declared_by = tuple(sorted({*(existing.declared_by if existing else ()), entry.file}))
        using = tuple(sorted(users.get(name, set())))
        found[key] = Resource(
            key=key,
            kind=entry.kind,
            # What the repository calls it, never the image (§4).
            name=name,
            display_name=existing.display_name if existing else entry.display_name,
            declared_by=declared_by,
            users=using,
            home_unit=_home(using),
            children=tuple(
                # A child is of its parent's kind: a database on a server, a model on a provider.
                ResourceChild(
                    key=f"{key}/{entry.kind.value}:{alias_key(child)}",
                    kind=entry.kind,
                    name=child,
                    owner=owner,
                )
                for child, owner in sorted(children.get(alias_key(name), {}).items())
            ),
        )
    resources = [found[key] for key in sorted(found)]
    return resources, _uses(namings, resources, canonical, parent_of), rows


def _canonical(declared: list[_Declared]) -> dict[str, str]:
    """The name each declared name resolves to: its own, or the deployment's when a catalogue word
    names a kind of thing the deployment runs exactly one of (`postgresql` in a `pom.xml` is the
    compose file's `db` when that is the one PostgreSQL there)."""
    named: dict[tuple[ResourceKind, str], set[str]] = {}
    for entry in declared:
        if entry.named:
            named.setdefault((entry.kind, entry.display_name), set()).add(entry.name)
    canonical: dict[str, str] = {}
    for entry in sorted(declared, key=lambda entry: (not entry.named, entry.name)):
        alias = alias_key(entry.name)
        if alias in canonical:
            continue
        instances = named.get((entry.kind, entry.display_name), set())
        canonical[alias] = entry.name if entry.named or len(instances) != 1 else next(iter(instances))
    return canonical


def _named(anchors: list[Anchor], canonical: dict[str, str], parent_of: dict[str, str]) -> list[_Naming]:
    """Every anchor naming a resource or a child by name: a host in a setting, a reference in an AppHost,
    a configuration key that is a child's own name (`textEmbeddingModel`)."""
    found = []
    for anchor in anchors:
        if anchor.role is not AnchorRole.USE or anchor.family not in (
            AnchorFamily.SERVICE_NAMES,
            AnchorFamily.CONFIGURATION,
        ):
            continue
        spelled = (
            [anchor.key, *re.split(r"__|[.:\[\]]", anchor.key)]
            if anchor.family is AnchorFamily.CONFIGURATION
            else [anchor.key]
        )
        name = next((s for s in spelled if alias_key(s) in canonical or alias_key(s) in parent_of), "")
        if name:
            found.append(
                _Naming(
                    anchor.unit,
                    name,
                    anchor.file,
                    anchor.line,
                    anchor.column,
                    anchor.key,
                    anchor.setting,
                    anchor.family,
                )
            )
    return found


def _uses(
    namings: list[_Naming], resources: list[Resource], canonical: dict[str, str], parent_of: dict[str, str]
) -> list[Use]:
    """Each naming as a use of the key it names, sorted, one per place."""
    keys = {alias_key(resource.name): resource.key for resource in resources}
    for resource in resources:
        for child in resource.children:
            keys[alias_key(child.name)] = child.key
    found = set()
    for naming in namings:
        if not naming.unit or naming.unit == ".":
            continue
        alias = alias_key(naming.name)
        target = keys.get(alias_key(canonical.get(alias, ""))) or keys.get(alias)
        if target is None:
            continue
        found.add(
            Use(naming.unit, target, naming.file, naming.line, naming.column, naming.key, naming.setting, naming.family)
        )
    return sorted(found, key=lambda use: (use.source, use.target, use.file, use.line, use.column, use.key, use.setting))


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


def _stores(anchors: list[Anchor]) -> tuple[list[_Declared], list[_Naming]]:
    """A store named only in a connection string: its scheme, its own name or its setting says what it is."""
    hosts = {
        (anchor.file, anchor.line): anchor
        for anchor in anchors
        if anchor.family is AnchorFamily.SERVICE_NAMES and anchor.role is AnchorRole.USE
    }
    found: list[_Declared] = []
    namings: list[_Naming] = []
    for anchor in anchors:
        if anchor.family is not AnchorFamily.DATA_ACCESS or anchor.role is not AnchorRole.USE:
            continue
        server = SERVER_PART.match(anchor.key)
        if server is not None:
            kind, display = classify(server.group(1))
            if kind is None and _CONNECTION_SETTING.search(anchor.setting):
                # A `Server=` inside a connection string is a database server whatever it is called.
                kind, display = ResourceKind.DB, "SQL database"
            if kind is not None:
                found.append(_Declared(server.group(1), kind, display, anchor.file, anchor.line))
                namings.append(_naming(anchor, server.group(1)))
        elif ": " in anchor.key:
            # `ConnectionStrings.EventBus: amqp` — the scheme is the kind, and the host written on
            # the same line is the name the repository gave it.
            host = hosts.get((anchor.file, anchor.line))
            kind, display = classify(host.key) if host else (None, "")
            if host and kind is None:
                kind, display = classify_word(anchor.key.rsplit(": ", 1)[1].split(":")[0])
            if host and kind is not None:
                found.append(_Declared(host.key, kind, display, anchor.file, anchor.line))
                namings.append(_naming(anchor, host.key))
    return found, namings


def _drivers(scan: Scan, units: list[Unit]) -> tuple[list[_Declared], list[_Naming]]:
    """A driver a manifest depends on declares a resource of its kind, used by that unit (§7)."""
    found: list[_Declared] = []
    namings: list[_Naming] = []
    for unit in units:
        if not unit.manifest:
            continue
        for dependency in dependencies(scan, unit.manifest):
            word = DRIVERS.get(dependency.lower())
            if word is None:
                continue
            kind, display = CATALOGUE[word]
            line = _line_of(scan.text(unit.manifest), dependency)
            found.append(_Declared(word, kind, display, unit.manifest, line, named=False))
            namings.append(_Naming(unit.id, word, unit.manifest, line, 1, dependency, "", AnchorFamily.BUILD_MANIFEST))
    return found, namings


def _clients(anchors: list[Anchor]) -> tuple[list[_Declared], list[_Naming]]:
    """A vector-store client type in code declares a db: nothing else names it (§7)."""
    found: list[_Declared] = []
    namings: list[_Naming] = []
    for anchor in anchors:
        if anchor.family is not AnchorFamily.DATA_ACCESS or anchor.role is not AnchorRole.USE or "=" in anchor.key:
            continue
        if ": " in anchor.key or "vectorstore" not in word_of(anchor.key):
            continue
        kind, display = CATALOGUE["vectorstore"]
        found.append(_Declared("vectorstore", kind, display, anchor.file, anchor.line, named=False))
        namings.append(_naming(anchor, "vectorstore"))
    return found, namings


def _configured(anchors: list[Anchor], names: Names) -> tuple[list[_Declared], list[_Naming]]:
    """A third party a key names: `spring.ai.openai.api-key`, `OTEL_EXPORTER_OTLP_ENDPOINT` (§7).

    The name is the segment that named it, never the whole key: `spring.ai.openai.api-key` and
    `OPENAI_API_KEY` are two keys about one thing, and a key is a way of reaching a resource rather
    than a second resource to draw. The first known segment wins, because `spring.ai.azure.openai`
    names an OpenAI rather than an Azure.
    """
    found: list[_Declared] = []
    namings: list[_Naming] = []
    for anchor in anchors:
        if anchor.family is not AnchorFamily.CONFIGURATION:
            continue
        segments = [segment for segment in re.split(r"[.:_\-\[\]]+", anchor.key) if segment]
        known = next((segment for segment in segments if word_of(segment) in CATALOGUE), "")
        if known and not names.unit_of(known):
            kind, display = CATALOGUE[word_of(known)]
            found.append(_Declared(known, kind, display, anchor.file, anchor.line, named=False))
            namings.append(_naming(anchor, known))
    return found, namings


def _naming(anchor: Anchor, name: str) -> _Naming:
    return _Naming(
        anchor.unit,
        name,
        anchor.file,
        anchor.line,
        anchor.column,
        anchor.key,
        anchor.setting or anchor.key,
        anchor.family,
    )


def _line_of(text: str, needle: str) -> int:
    """The first line naming *needle*, so a dependency's site is where it is written; 1 when unknown."""
    lowered = needle.lower()
    for number, line in enumerate(text.split("\n"), 1):
        if lowered in line.lower():
            return number
    return 1


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


def _home(users: Sequence[str]) -> str:
    """Whose resource it is: its only user, else the directory its users share (§7). A unit here; the
    placement resolves it to a component. A content-defining declaration — a migration directory — names
    a database and never the server that holds it, so it decides a child's owner and not a parent's home."""
    if len(users) == 1:
        return users[0]
    if not users:
        return ""
    common = os.path.commonpath([unit if unit != "." else "" for unit in users]) if len(set(users)) > 1 else users[0]
    return common.replace(os.sep, "/") or "."
