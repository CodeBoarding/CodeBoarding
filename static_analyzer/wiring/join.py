"""Matching a use of a name to the unit that answers to it, in the order the page sets out.

A join is the whole of the judgement: an anchor says `lb://vets-service` is written in the gateway,
the unit table says exactly one unit answers to that name, and the pair becomes one edge of one
kind. Everything that does not join is a diagnostic — an unresolved use, a definition nobody reads,
a name two units answer to — because a wiring layer that drops what it could not explain is a
wiring layer nobody can debug.

The order matters (§6 rule 3): units first, because a name means nothing until the table exists;
then the names configuration and deployment use; then the routes, whose target is itself a name.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass

from static_analyzer.cfg import EdgeKind
from static_analyzer.wiring.anchors.keys import Names
from static_analyzer.wiring.manifests import project_references
from static_analyzer.wiring.scan import FileKind, Scan
from static_analyzer.wiring.units import alias_key
from static_analyzer.wiring_results import Anchor, AnchorFamily, AnchorRole, Diagnostic, DiagnosticCode, Tier, Unit

#: What a unit that others register with, fetch configuration from or report to looks like when it
#: is code of this repository rather than a stock image. The annotation is the declaration.
REGISTRY_MARKERS = ("@EnableEurekaServer", "@EnableDiscoveryServer")
CONFIG_SERVER_MARKERS = ("@EnableConfigServer",)

#: What an environment variable is called, wherever it is set: a name carrying an underscore or a
#: capital. A compose service is `ci` and an image is `toolchain:local`; neither is a key anything
#: looks up, so neither is a definition that can go unread.
_ENVIRONMENT_KEY = re.compile(r"^(?=.*[_A-Z])[A-Za-z_][A-Za-z0-9_]*$")

#: A use whose key says what the connection is for, whatever the target turns out to be.
_REGISTERS = re.compile(r"(?i)eureka|discovery|consul|registry|zookeeper")
_FETCHES_CONFIG = re.compile(r"(?i)config[\s._-]*server|configserver|spring\.config\.import|configuration[\s._-]*uri")
_REPORTS = re.compile(r"(?i)zipkin|jaeger|otlp|opentelemetry|tracing|telemetry|prometheus|metrics|sleuth")


@dataclass(frozen=True)
class Join:
    """One resolved connection between two units, and the place that declared it."""

    source: str
    target: str
    kind: EdgeKind
    file: str
    line: int
    column: int
    key: str
    family: AnchorFamily


def join(scan: Scan, units: list[Unit], anchors: list[Anchor]) -> tuple[list[Join], list[Diagnostic]]:
    """Every edge the files declare between two units of this repository, and what did not join."""
    names = Names(units)
    roles = _roles(anchors)
    joins: list[Join] = [*_dependencies(scan, units), *_by_name(anchors, names, roles)]
    diagnostics = _unresolved(anchors, names) + _unused(anchors)
    return sorted(set(joins), key=_order), diagnostics


def _dependencies(scan: Scan, units: list[Unit]) -> list[Join]:
    """What a build manifest says one unit needs from another: a project reference, a workspace name."""
    by_directory = {unit.dir: unit for unit in units}
    declared: list[Join] = []
    for unit in units:
        if not unit.manifest:
            continue
        kind = scan.files[unit.manifest].kind if unit.manifest in scan.files else None
        if kind is FileKind.DOTNET_PROJECT:
            for reference in project_references(scan, unit.manifest):
                target = by_directory.get(reference.rsplit("/", 1)[0] if "/" in reference else ".")
                if target is not None and target.id != unit.id:
                    declared.append(
                        Join(
                            source=unit.id,
                            target=target.id,
                            kind=EdgeKind.DEPENDS_ON,
                            file=unit.manifest,
                            line=scan.line_of(unit.manifest, reference.rsplit("/", 1)[-1]),
                            column=1,
                            key=reference,
                            family=AnchorFamily.BUILD_MANIFEST,
                        )
                    )
        elif kind is FileKind.NPM:
            declared += _npm_dependencies(scan, unit, units)
    return declared


def _npm_dependencies(scan: Scan, unit: Unit, units: list[Unit]) -> list[Join]:
    """A workspace package that depends on a sibling by the name that sibling declares."""
    manifest = scan.json_object(unit.manifest)
    siblings = {alias_key(alias): other for other in units for alias in other.aliases if other.id != unit.id}
    declared = []
    for section in ("dependencies", "devDependencies", "peerDependencies"):
        for name in (manifest.get(section) or {}) if isinstance(manifest.get(section), dict) else {}:
            target = siblings.get(alias_key(str(name)))
            if target is not None:
                declared.append(
                    Join(
                        source=unit.id,
                        target=target.id,
                        kind=EdgeKind.DEPENDS_ON,
                        file=unit.manifest,
                        line=scan.line_of(unit.manifest, f'"{name}"'),
                        column=1,
                        key=str(name),
                        family=AnchorFamily.BUILD_MANIFEST,
                    )
                )
    return declared


def _by_name(anchors: list[Anchor], names: Names, roles: dict[str, EdgeKind]) -> list[Join]:
    """Every use of a name exactly one unit answers to, as an edge of the kind the use implies."""
    routing = {
        (anchor.file, anchor.unit) for anchor in anchors if anchor.role is AnchorRole.DEF and anchor.tier is Tier.T2
    }
    joined = []
    for anchor in anchors:
        if anchor.family is not AnchorFamily.SERVICE_NAMES or anchor.role is not AnchorRole.USE:
            continue
        # A key nobody wrote down literally is a guess, and a guess is never an edge (§6 rule 4).
        if anchor.tier is Tier.T3:
            continue
        target = names.unit_of(anchor.norm_key)
        if not target or not anchor.unit or target == anchor.unit:
            continue
        joined.append(
            Join(
                source=anchor.unit,
                target=target,
                kind=roles.get(target, _kind_of(anchor, routing)),
                file=anchor.file,
                line=anchor.line,
                column=anchor.column,
                key=anchor.key,
                family=anchor.family,
            )
        )
    return joined


def _kind_of(anchor: Anchor, routing: set[tuple[str, str]]) -> EdgeKind:
    """What a use is for, read from the key that expresses it and the route it belongs to (§5).

    Why not the anchor's own tier: a route has two halves and only the path half is the template, so
    the name a gateway forwards to is an ordinary T1 name sitting beside a T2 route. Why the unit and
    not the file alone: an Aspire AppHost declares its own routes and configures every other service
    in one file, and a variable it sets on one service is that service calling another, not a route.
    A key saying what the connection is for outranks both, because a gateway registers itself and
    fetches its own configuration like every other service.
    """
    if _FETCHES_CONFIG.search(anchor.key):
        return EdgeKind.FETCHES_CONFIG
    if _REGISTERS.search(anchor.key):
        return EdgeKind.REGISTERS_WITH
    if _REPORTS.search(anchor.key):
        return EdgeKind.REPORTS_TO
    return EdgeKind.ROUTES_TO if (anchor.file, anchor.unit) in routing else EdgeKind.CALLS_HTTP


def _roles(anchors: list[Anchor]) -> dict[str, EdgeKind]:
    """The units that declare themselves a registry or a configuration server, and what that makes them."""
    roles: dict[str, EdgeKind] = {}
    for anchor in anchors:
        if anchor.role is not AnchorRole.DEF or not anchor.unit:
            continue
        if any(marker in anchor.key for marker in REGISTRY_MARKERS):
            roles[anchor.unit] = EdgeKind.REGISTERS_WITH
        elif any(marker in anchor.key for marker in CONFIG_SERVER_MARKERS):
            roles[anchor.unit] = EdgeKind.FETCHES_CONFIG
    return roles


def _unresolved(anchors: list[Anchor], names: Names) -> list[Diagnostic]:
    """Every use that joined nothing: no unit answers to the name, or several do.

    The two are different failures and the page reports them as different rows (§8): a name nobody
    answers to is the shape a resolver would one day read, while a name two units answer to is an
    ambiguity that names both candidates and draws nothing (§6 rule 7).
    """
    found = []
    for anchor in anchors:
        if anchor.family is not AnchorFamily.SERVICE_NAMES or anchor.role is not AnchorRole.USE:
            continue
        if names.unit_of(anchor.norm_key) or not anchor.norm_key:
            continue
        # An annotation is a role its unit takes on, not a name anything answers to.
        if anchor.key.startswith("@"):
            continue
        where = f"{anchor.key} in {anchor.file}:{anchor.line}"
        candidates = names.candidates(anchor.norm_key)
        if len(candidates) > 1:
            found.append(
                Diagnostic(
                    code=DiagnosticCode.AMBIGUOUS_KEY,
                    message=f"{where} names {len(candidates)} units and draws nothing: {', '.join(candidates)}",
                    paths=(anchor.file,),
                )
            )
        else:
            found.append(
                Diagnostic(
                    code=DiagnosticCode.UNRESOLVED_USE,
                    message=f"{where} names no unit of this repository",
                    paths=(anchor.file,),
                )
            )
    return found


def _unused(anchors: list[Anchor]) -> list[Diagnostic]:
    """Every environment key a deployment sets that no code here reads.

    Only a key: a service name, a port and an image are definitions too, but nothing looks them up
    by key — they are joined by name — so a service nobody mentions is ordinary rather than a
    finding, and reporting one would bury the keys that are.
    """
    read = {anchor.norm_key for anchor in anchors if anchor.role is AnchorRole.USE}
    defined: dict[str, list[Anchor]] = defaultdict(list)
    for anchor in anchors:
        if anchor.family is not AnchorFamily.DEPLOYMENT or anchor.role is not AnchorRole.DEF:
            continue
        if anchor.norm_key and _ENVIRONMENT_KEY.match(anchor.key):
            defined[anchor.norm_key].append(anchor)
    return [
        Diagnostic(
            code=DiagnosticCode.UNUSED_DEFINITION,
            message=f"{anchors_for[0].key} is set in {anchors_for[0].file} and nothing here reads it",
            paths=tuple(sorted({anchor.file for anchor in anchors_for})),
        )
        for key, anchors_for in sorted(defined.items())
        if key not in read
    ]


def _order(found: Join) -> tuple[str, str, str, str, int]:
    return found.source, found.target, found.kind.value, found.file, found.line
