"""What a deployment file says at run time: the environment it sets, and what it depends on.

A compose service, a Kubernetes container and an Aspire resource all do the same three things — set
a variable, name another service, wait for one — in three notations. Each becomes an anchor on the
unit it is about, in the file and at the line where it is written, so a join can pair the key a
deployment defines with the code that reads it. A ConfigMap or Secret is about every workload that
pulls it in through `envFrom` or `valueFrom`, not about the file it happens to be written in.
"""

from __future__ import annotations

import re

from static_analyzer.wiring.anchors.keys import Names, Owners, env_key, hosts_in
from static_analyzer.wiring.compose import ComposeProject, ComposeService
from static_analyzer.wiring.images import image_ref
from static_analyzer.wiring.scan import FileKind, Scan, listing, mapping, parent_dir
from static_analyzer.wiring.topology import ASPIRE_HOST
from static_analyzer.wiring.units import alias_key
from static_analyzer.wiring_results import Anchor, AnchorFamily, AnchorRole, Tier

#: `var basket = builder.AddProject<Projects.Basket_API>("basket-api")`, and the endpoint of one.
_ASPIRE_RESOURCE = re.compile(
    r"(?:var|let)\s+(\w+)\s*=\s*[\w.]*\b(?:AddProject\s*<[^>]*>|AddProject|AddNpmApp|AddViteApp|AddNodeApp|"
    r"AddPythonApp|AddUvicornApp|AddJavaApp|AddGolangApp|AddDockerfile|AddContainer|AddYarp|AddRedis|"
    r"AddRabbitMQ|AddPostgres|AddSqlServer|AddMongoDB|AddKafka|AddAzureServiceBus|AddOllama|AddFoundry|"
    r"AddDatabase|AddDeployment|AddModel|AddConnectionString)"
    r"\s*\(\s*\"([^\"]+)\""
)
_ASPIRE_ENDPOINT = re.compile(r"(?:var|let)\s+(\w+)\s*=\s*(\w+)\s*\.\s*(?:GetEndpoint|GetConnectionString)\s*\(")
#: `.WithEnvironment("Identity__Url", identityEndpoint)` and `.WithReference(basketApi)`. What they
#: configure is what their statement is about, so the receiver is read from the statement, not here.
_WITH_ENVIRONMENT = re.compile(r"\.\s*WithEnvironment\s*\(\s*\"([^\"]+)\"\s*,\s*([^;]*?)\)")
_WITH_REFERENCE = re.compile(r"\.\s*(?:WithReference|WaitFor)\s*\(\s*(\w+)")
_DECLARED = re.compile(r"(?:var|let)\s+(\w+)\s*=")
_RECEIVER = re.compile(r"\s*(\w+)\s*\.")
_ASPIRE_CALL = re.compile(r"\bAdd(?:Project|NpmApp|Container|Yarp|Connection)")
_HOLDERS = ("ConfigMap", "Secret")


def read(scan: Scan, projects: list[ComposeProject], owners: Owners, names: Names) -> list[Anchor]:
    """Every anchor the deployment topology writes down."""
    return [
        *_compose(projects, names),
        *_kubernetes(scan, owners, names),
        *_aspire(scan, owners, names),
    ]


def aspire_resources(text: str) -> dict[str, str]:
    """Each variable of an AppHost bound to the resource it registers, endpoints included."""
    resources = {variable: resource for variable, resource in _ASPIRE_RESOURCE.findall(text)}
    for variable, origin in _ASPIRE_ENDPOINT.findall(text):
        if origin in resources:
            resources[variable] = resources[origin]
    return resources


def _compose(projects: list[ComposeProject], names: Names) -> list[Anchor]:
    found: list[Anchor] = []
    for project in projects:
        for service in project.services:
            unit = names.unit_of(service.name)
            found.append(_declaration(service.name, alias_key(service.name), service, unit))
            image = image_ref(service.image).repository
            if image and not unit:
                # An image nothing here builds is how a stock service is declared: `openzipkin/zipkin`.
                found.append(_declaration(service.image, alias_key(image), service, unit))
            for port in service.ports:
                found.append(_declaration(port, port.replace(" ", ""), service, unit))
            for entry in service.environment:
                found.append(
                    Anchor(
                        family=AnchorFamily.DEPLOYMENT,
                        role=AnchorRole.DEF,
                        key=entry.key,
                        norm_key=env_key(entry.key),
                        file=entry.file,
                        line=entry.line,
                        unit=unit,
                    )
                )
                found += _hosts(entry.value, entry.key, entry.file, entry.line, unit)
            for target in (*service.depends_on, *service.links):
                found.append(
                    Anchor(
                        family=AnchorFamily.SERVICE_NAMES,
                        role=AnchorRole.USE,
                        key=target,
                        norm_key=alias_key(target),
                        file=service.files[0],
                        line=service.line,
                        unit=unit,
                        setting="depends_on",
                    )
                )
    return found


def _declaration(key: str, norm_key: str, service: ComposeService, unit: str) -> Anchor:
    """What a compose service says about itself, at the line where it is declared."""
    return Anchor(
        family=AnchorFamily.DEPLOYMENT,
        role=AnchorRole.DEF,
        key=key,
        norm_key=norm_key,
        file=service.files[0],
        line=service.line,
        unit=unit,
    )


def _kubernetes(scan: Scan, owners: Owners, names: Names) -> list[Anchor]:
    """Container `env`, and what a ConfigMap or Secret holds, on every workload that pulls it in."""
    documents = [
        (path, document)
        for path in scan.paths_of(FileKind.YAML)
        if "apiVersion" in scan.text(path) and "kind:" in scan.text(path)
        for document in scan.documents(path)
    ]
    holders: dict[str, list[tuple[str, dict]]] = {}
    for path, document in documents:
        if document.get("kind") in _HOLDERS:
            name = str(mapping(document.get("metadata")).get("name") or "")
            data = {**mapping(document.get("data")), **mapping(document.get("stringData"))}
            holders.setdefault(name, []).append((path, data))
    found: list[Anchor] = []
    consumed: set[tuple[str, str]] = set()
    for path, document in documents:
        kind = document.get("kind")
        if kind in _HOLDERS:
            continue
        metadata = mapping(document.get("metadata"))
        unit = names.unit_of(str(metadata.get("name") or "")) or owners.of(path)
        for container in _containers(kind, mapping(document.get("spec")), metadata):
            found += _container_env(scan, path, container, unit, holders, consumed)
    # A ConfigMap nobody pulls in is read where it is written, about the unit whose file it is.
    for name, held in sorted(holders.items()):
        for held_path, data in held:
            if (name, held_path) not in consumed:
                found += _data_keys(scan, held_path, data, owners.of(held_path))
    return found


def _container_env(
    scan: Scan,
    path: str,
    container: dict,
    unit: str,
    holders: dict[str, list[tuple[str, dict]]],
    consumed: set[tuple[str, str]],
) -> list[Anchor]:
    found: list[Anchor] = []
    for entry in listing(container.get("env")):
        if not isinstance(entry, dict) or not isinstance(entry.get("name"), str):
            continue
        key = entry["name"]
        line = scan.line_of(path, key)
        value = entry.get("value")
        reference = mapping(entry.get("valueFrom"))
        for holder_kind in ("configMapKeyRef", "secretKeyRef"):
            pointer = mapping(reference.get(holder_kind))
            holder = _holder(holders, str(pointer.get("name") or ""), path)
            if holder is not None:
                held_path, data = holder
                consumed.add((str(pointer.get("name") or ""), held_path))
                value = data.get(str(pointer.get("key") or ""), value)
        found.append(
            Anchor(
                family=AnchorFamily.DEPLOYMENT,
                role=AnchorRole.DEF,
                key=key,
                norm_key=env_key(key),
                file=path,
                line=line,
                unit=unit,
            )
        )
        if isinstance(value, str):
            found += _hosts(value, key, path, line, unit)
    for entry in listing(container.get("envFrom")):
        if not isinstance(entry, dict):
            continue
        for holder_kind in ("configMapRef", "secretRef"):
            name = str(mapping(entry.get(holder_kind)).get("name") or "")
            holder = _holder(holders, name, path)
            if holder is not None:
                held_path, data = holder
                consumed.add((name, held_path))
                found += _data_keys(scan, held_path, data, unit)
    return found


def _holder(holders: dict[str, list[tuple[str, dict]]], name: str, path: str) -> tuple[str, dict] | None:
    """The ConfigMap or Secret a workload names: the one beside it when several share a name."""
    candidates = holders.get(name, [])
    beside = [held for held in candidates if parent_dir(held[0]) == parent_dir(path)]
    chosen = beside or candidates
    return chosen[0] if chosen else None


def _data_keys(scan: Scan, path: str, data: dict, unit: str) -> list[Anchor]:
    found: list[Anchor] = []
    lines = scan.key_lines(path)
    for key, value in data.items():
        line = lines.get(f"data.{key}") or lines.get(f"stringData.{key}") or scan.line_of(path, f"{key}:")
        found.append(
            Anchor(
                family=AnchorFamily.DEPLOYMENT,
                role=AnchorRole.DEF,
                key=str(key),
                norm_key=env_key(str(key)),
                file=path,
                line=line,
                unit=unit,
            )
        )
        if isinstance(value, str):
            found += _hosts(value, str(key), path, line, unit)
    return found


def _containers(kind: object, specification: dict, metadata: dict) -> list[dict]:
    if kind == "CronJob":
        specification = mapping(mapping(specification.get("jobTemplate")).get("spec"))
    template = specification.get("template")
    if not isinstance(template, dict):
        template = {"spec": specification, "metadata": metadata} if kind == "Pod" else {}
    containers = listing(mapping(template.get("spec")).get("containers"))
    return [container for container in containers if isinstance(container, dict)]


def _aspire(scan: Scan, owners: Owners, names: Names) -> list[Anchor]:
    """An AppHost is a manifest written in C#: its resources, their variables and their references."""
    found: list[Anchor] = []
    for path in scan.paths_of(FileKind.DOTNET_PROJECT):
        if not any(marker in scan.text(path) for marker in ASPIRE_HOST):
            continue
        for source in scan.sources(owners.of(path) if owners.of(path) != "." else ""):
            text = scan.text(source)
            if not _ASPIRE_CALL.search(text):
                continue
            found += _aspire_source(source, text, names)
    return found


def _aspire_source(source: str, text: str, names: Names) -> list[Anchor]:
    """One statement at a time: what a call configures is what its own statement is about."""
    resources = aspire_resources(text)
    found: list[Anchor] = []
    for start, statement in _statements(text):
        declared, receiver = _DECLARED.search(statement), _RECEIVER.match(statement)
        subject = declared.group(1) if declared else (receiver.group(1) if receiver else "")
        unit = names.unit_of(resources.get(subject, subject))
        if not unit:
            continue
        for match in _WITH_ENVIRONMENT.finditer(statement):
            line = text.count("\n", 0, start + match.start()) + 1
            found.append(
                Anchor(
                    family=AnchorFamily.DEPLOYMENT,
                    role=AnchorRole.DEF,
                    key=match.group(1),
                    norm_key=env_key(match.group(1)),
                    file=source,
                    line=line,
                    unit=unit,
                )
            )
            found += _referenced(match.group(2), resources, source, line, unit, match.group(1))
        for match in _WITH_REFERENCE.finditer(statement):
            line = text.count("\n", 0, start + match.start()) + 1
            found += _referenced(match.group(1), resources, source, line, unit, "WithReference")
    return found


def _referenced(value: str, resources: dict[str, str], source: str, line: int, unit: str, setting: str) -> list[Anchor]:
    """The resources an argument names, through the variables the AppHost bound them to."""
    return [
        Anchor(
            family=AnchorFamily.SERVICE_NAMES,
            role=AnchorRole.USE,
            key=resources[token],
            norm_key=alias_key(resources[token]),
            file=source,
            line=line,
            unit=unit,
            setting=setting,
        )
        for token in dict.fromkeys(re.findall(r"\w+", value))
        if token in resources
    ]


def _statements(text: str) -> list[tuple[int, str]]:
    """Each statement with the offset it starts at: a chain of calls ends at its semicolon."""
    statements, start = [], 0
    for index, character in enumerate(text):
        if character == ";":
            statements.append((start, text[start:index]))
            start = index + 1
    statements.append((start, text[start:]))
    return statements


def _hosts(value: str, key: str, file: str, line: int, unit: str) -> list[Anchor]:
    """The service names a value holds, each one a use of that name by this unit, under the key that set it."""
    return [
        Anchor(
            family=AnchorFamily.SERVICE_NAMES,
            role=AnchorRole.USE,
            key=host,
            norm_key=alias_key(host),
            file=file,
            line=line,
            unit=unit,
            tier=Tier.T1,
            setting=key,
        )
        for host in hosts_in(value, key)
    ]
