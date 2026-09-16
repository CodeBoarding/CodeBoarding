"""Where a gateway sends a request: nginx, Spring Cloud Gateway, YARP, a Kubernetes Ingress.

A route has two halves, and both are anchors: the path it matches, which is a template and so T2,
and the service it forwards to, which is an ordinary name. An nginx `upstream` is the third: a name
declared in one file and used by a `*_pass` in another, including through an `include`.
"""

from __future__ import annotations

import re
from collections.abc import Sequence

from static_analyzer.wiring.anchors.configuration import entries
from static_analyzer.wiring.anchors.deployment import aspire_resources
from static_analyzer.wiring.anchors.keys import Owners, service_host
from static_analyzer.wiring.scan import FileKind, Scan, parent_dir, repo_path
from static_analyzer.wiring.units import alias_key
from static_analyzer.wiring_results import Anchor, AnchorFamily, AnchorRole, Tier

UPSTREAM = re.compile(r"\bupstream\s+([\w.-]+)\s*\{([^}]*)\}")
UPSTREAM_SERVER = re.compile(r"\bserver\s+([^;\s]+)")
PASS = re.compile(r"\b(proxy_pass|uwsgi_pass|grpc_pass|fastcgi_pass)\s+([^;\s]+)\s*;")
LOCATION = re.compile(r"\blocation\s+(?:[=~^*]+\s*)?([^{\s]+)\s*\{")
INCLUDE = re.compile(r"(?m)^\s*include\s+([^;\s]+)\s*;")

#: `yarp.AddRoute("/catalog-api/api/catalog/items", catalogCluster)` beside `AddCluster(catalogApi)`.
ADD_ROUTE = re.compile(r"\bAddRoute\s*\(\s*\"([^\"]+)\"\s*,\s*(\w+)")
ADD_CLUSTER = re.compile(r"(?:var|let)\s+(\w+)\s*=\s*\w+\s*\.\s*AddCluster\s*\(\s*(\w+)")

#: A Spring Cloud Gateway route table, flattened: `...routes[0].uri` and `...routes[0].predicates[0]`.
GATEWAY_ROUTE = re.compile(r"(?i)routes\[(\d+)\]\.(uri|id|predicates\[\d+\])$")


def read(scan: Scan, sources: Sequence[tuple[str, str]], owners: Owners) -> list[Anchor]:
    """Every route a gateway declares, and every upstream a route forwards to."""
    return [
        *_nginx(scan, owners),
        *_gateway_tables(scan, owners),
        *_yarp(sources, owners),
        *_ingress(scan, owners),
    ]


def _nginx(scan: Scan, owners: Owners) -> list[Anchor]:
    found: list[Anchor] = []
    upstreams = _upstreams(scan)
    for path in scan.paths_of(FileKind.NGINX):
        text = scan.text(path)
        if "_pass" not in text:
            continue
        unit = owners.of(path)
        for match in PASS.finditer(text):
            directive, target = match.groups()
            line = text.count("\n", 0, match.start()) + 1
            host = re.sub(r"^[a-z]+://", "", target).split("/")[0].split(":")[0]
            # The name written is a name: an upstream's own servers may be a unix socket or a
            # loopback port, which name no unit, and the pass still says where it sends.
            through = [host, *(service_host(server) for server in upstreams.get(host, []))]
            for resolved in dict.fromkeys(name for name in through if name):
                found.append(
                    Anchor(
                        family=AnchorFamily.SERVICE_NAMES,
                        role=AnchorRole.USE,
                        key=f"{directive} {target}",
                        norm_key=alias_key(resolved),
                        file=path,
                        line=line,
                        unit=unit,
                    )
                )
            location = [found_at.group(1) for found_at in LOCATION.finditer(text, 0, match.start())]
            if location:
                found.append(_route(location[-1], path, line, unit))
    return found


def _upstreams(scan: Scan) -> dict[str, list[str]]:
    """Every `upstream` block's servers, read from every nginx file and the files they include."""
    upstreams: dict[str, list[str]] = {}
    for path in scan.paths_of(FileKind.NGINX):
        for name, body in UPSTREAM.findall(scan.text(path)):
            upstreams.setdefault(name, []).extend(UPSTREAM_SERVER.findall(body))
        for included in INCLUDE.findall(scan.text(path)):
            target = _included(scan, path, included.strip('"'))
            if target:
                for name, body in UPSTREAM.findall(scan.text(target)):
                    upstreams.setdefault(name, []).extend(UPSTREAM_SERVER.findall(body))
    return upstreams


def _included(scan: Scan, path: str, included: str) -> str:
    """The file an `include` names: a path here, or the one whose tail matches a deployed path.

    Why the tail: an nginx configuration includes the path it will have once installed
    (`/etc/nginx/zulip-include/upstreams`), which is not where the repository keeps it.
    """
    direct = repo_path(parent_dir(path), included)
    if direct and scan.has_file(direct):
        return direct
    tail = "/".join(included.strip("/").split("/")[-2:])
    candidates = [candidate for candidate in scan.paths_of(FileKind.NGINX) if candidate.endswith(tail)]
    return candidates[0] if len(candidates) == 1 else ""


def _gateway_tables(scan: Scan, owners: Owners) -> list[Anchor]:
    """A Spring Cloud Gateway route table: the predicate is the template, the uri names the unit."""
    found: list[Anchor] = []
    for path in scan.paths_of(FileKind.SPRING_CONFIG, FileKind.PROPERTIES):
        unit = owners.of(path)
        for key, value, line in entries(scan, path):
            match = GATEWAY_ROUTE.search(key)
            if match is None or match.group(2) == "id":
                continue
            if match.group(2) == "uri":
                host = service_host(value)
                if host:
                    found.append(
                        Anchor(
                            family=AnchorFamily.SERVICE_NAMES,
                            role=AnchorRole.USE,
                            key=value,
                            norm_key=alias_key(host),
                            file=path,
                            line=line,
                            unit=unit,
                        )
                    )
            elif value.lower().startswith("path="):
                found.append(_route(value.split("=", 1)[1], path, line, unit))
    return found


def _yarp(sources: Sequence[tuple[str, str]], owners: Owners) -> list[Anchor]:
    """A reverse proxy configured in code: each route's path, and the resource its cluster is."""
    found: list[Anchor] = []
    for path, text in sources:
        if "AddRoute(" not in text:
            continue
        unit = owners.of(path)
        resources = aspire_resources(text)
        # A cluster built from a parameter of an extension method names no resource in this file;
        # binding a method's parameters to its call site is the join's work, not a name.
        cluster_of = {
            variable: resources[resource] for variable, resource in ADD_CLUSTER.findall(text) if resource in resources
        }
        for match in ADD_ROUTE.finditer(text):
            route, cluster = match.groups()
            line = text.count("\n", 0, match.start()) + 1
            found.append(_route(route, path, line, unit))
            target = cluster_of.get(cluster, "")
            if target:
                found.append(
                    Anchor(
                        family=AnchorFamily.SERVICE_NAMES,
                        role=AnchorRole.USE,
                        key=target,
                        norm_key=alias_key(target),
                        file=path,
                        line=line,
                        unit=unit,
                    )
                )
    return found


def _ingress(scan: Scan, owners: Owners) -> list[Anchor]:
    found: list[Anchor] = []
    for path in scan.paths_of(FileKind.YAML):
        text = scan.text(path)
        if "Ingress" not in text or "apiVersion" not in text:
            continue
        for document in scan.documents(path):
            if document.get("kind") != "Ingress":
                continue
            specification = document.get("spec") if isinstance(document.get("spec"), dict) else {}
            for rule in (specification or {}).get("rules") or []:
                found += _ingress_rule(scan, path, rule, owners.of(path))
    return found


def _ingress_rule(scan: Scan, path: str, rule: object, unit: str) -> list[Anchor]:
    if not isinstance(rule, dict):
        return []
    found = []
    start = max(scan.text(path).find("kind: Ingress"), 0)
    for entry in ((rule.get("http") or {}).get("paths") or []) if isinstance(rule.get("http"), dict) else []:
        if not isinstance(entry, dict):
            continue
        service = ((entry.get("backend") or {}).get("service") or {}).get("name")
        route = entry.get("path")
        if isinstance(route, str):
            found.append(_route(route, path, scan.line_of(path, route, start), unit))
        if isinstance(service, str):
            found.append(
                Anchor(
                    family=AnchorFamily.SERVICE_NAMES,
                    role=AnchorRole.USE,
                    key=service,
                    norm_key=alias_key(service),
                    file=path,
                    line=scan.line_of(path, service, start),
                    unit=unit,
                )
            )
    return found


def _route(route: str, path: str, line: int, unit: str) -> Anchor:
    """The path half of a route: a template, so T2, with its parameters collapsed."""
    collapsed = re.sub(r"\{[^}]*\}|\*\*|\*|:\w+", "{}", route)
    template = collapsed.split("?")[0].rstrip("/") or "/"
    return Anchor(
        family=AnchorFamily.SERVICE_NAMES,
        role=AnchorRole.DEF,
        key=route,
        norm_key=template.lower(),
        file=path,
        line=line,
        unit=unit,
        tier=Tier.T2,
    )
