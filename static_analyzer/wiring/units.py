"""The unit table: every directory this repository builds or deploys, and the names it answers to.

One directory is one unit however many files declare it, so a Maven module a compose file runs is
one unit carrying both names, not two units answering to the same ones. The readers run in
dependency order (§6 rule 3): build manifests first, then the images this repository builds, then
the deployment files, because a service that only runs `langfuse/langfuse` becomes a directory only
once something here says which directory builds that image.
"""

from __future__ import annotations

import re
from collections import defaultdict

from static_analyzer.wiring import manifests, topology
from static_analyzer.wiring.compose import compose_projects
from static_analyzer.wiring.images import ImageIndex
from static_analyzer.wiring.manifests import Declaration
from static_analyzer.wiring.scan import Scan
from static_analyzer.wiring_results import DiagnosticCode, Unit, UnitKind

#: Kinds that say a file runs a unit rather than builds it, which is what makes a variant a variant.
DEPLOYMENT_KINDS = frozenset(
    {UnitKind.COMPOSE, UnitKind.SKAFFOLD, UnitKind.K8S, UnitKind.HELM, UnitKind.ASPIRE, UnitKind.DOCKER}
)

_KIND_ORDER = list(UnitKind)
_NOT_A_NAME = re.compile(r"[^a-z0-9]")


def build_units(scan: Scan, repo_name: str) -> list[Unit]:
    """Every unit of the repository, sorted by directory. Diagnostics accumulate on the scan."""
    reading = manifests.build_manifests(scan)
    projects = compose_projects(scan)

    index = ImageIndex()
    for build in (
        *reading.images,
        *topology.skaffold_builds(scan),
        *topology.compose_builds(scan, projects),
        *topology.workflow_builds(scan),
    ):
        index.add(build)
    for image, directories, files in index.ambiguous():
        scan.diagnose(DiagnosticCode.AMBIGUOUS_IMAGE, f"{image} is built from {' and '.join(directories)}", *files)

    declarations = list(reading.declarations)
    for found in (
        topology.skaffold_units(scan, index),
        topology.compose_units(scan, projects, index, repo_name),
        topology.kubernetes(scan, index, repo_name),
        topology.helm(scan, index, repo_name),
        topology.aspire(scan),
    ):
        declarations += found.declarations
    return _table(scan, _built_here(scan, declarations))


def _built_here(scan: Scan, declarations: list[Declaration]) -> list[Declaration]:
    """Drop what only a deployment file says about the repository root.

    A build whose context is the whole tree names the repository itself, which is a unit only when
    a manifest here builds it: otherwise a CI image that mounts the source is read as the product.
    """
    if any(not d.directory and d.kind not in DEPLOYMENT_KINDS for d in declarations):
        return declarations
    rootless = [d for d in declarations if d.directory or d.kind not in DEPLOYMENT_KINDS]
    dropped = sorted({build for d in declarations if d not in rootless for build in d.builds})
    if dropped:
        scan.diagnose(
            DiagnosticCode.IGNORED_MANIFEST,
            f"{', '.join(dropped)} builds the repository itself, which no manifest here builds",
            *dropped,
        )
    return rootless


def alias_key(alias: str) -> str:
    """A name as a join compares it: `mobile-bff`, `Mobile.BFF` and `mobilebff` are one name."""
    return _NOT_A_NAME.sub("", alias.lower())


def _table(scan: Scan, declarations: list[Declaration]) -> list[Unit]:
    grouped: dict[str, list[Declaration]] = defaultdict(list)
    for declaration in declarations:
        grouped[declaration.directory].append(declaration)

    units = []
    for directory, group in sorted(grouped.items()):
        group.sort(key=lambda declaration: (_KIND_ORDER.index(declaration.kind), declaration.manifest))
        aliases = [alias for declaration in group for alias in declaration.aliases]
        kind, manifest = group[0].kind, next((d.manifest for d in group if d.manifest), "")
        if not manifest:
            own = manifests.manifest_in(scan, directory)
            if own is not None:
                kind, manifest = min(kind, own.kind, key=_KIND_ORDER.index), own.manifest
                aliases += list(own.aliases)
            else:
                scan.diagnose(
                    DiagnosticCode.UNIT_WITHOUT_MANIFEST,
                    f"{directory or '.'} is deployed, and no manifest here builds it",
                    directory,
                )
        units.append(
            Unit(
                id=directory or ".",
                dir=directory or ".",
                kind=kind,
                manifest=manifest,
                aliases=tuple(sorted(dict.fromkeys(aliases))),
                builds=tuple(sorted({build for declaration in group for build in declaration.builds})),
                variant=_variant(group),
            )
        )
    _report_ambiguous_aliases(scan, units)
    return units


def _variant(group: list[Declaration]) -> tuple[str, ...]:
    """The profiles a unit runs under, when every deployment that runs it is profile-gated (§6)."""
    deployments = [declaration for declaration in group if declaration.kind in DEPLOYMENT_KINDS]
    if not deployments or any(not declaration.variant for declaration in deployments):
        return ()
    return tuple(sorted({profile for declaration in deployments for profile in declaration.variant}))


def _report_ambiguous_aliases(scan: Scan, units: list[Unit]) -> None:
    """Two units answering to one name: the name draws nothing, and both candidates are named (§6 rule 7)."""
    owners: dict[str, tuple[str, list[str]]] = {}
    for unit in units:
        for alias in unit.aliases:
            key = alias_key(alias)
            if not key:
                continue
            written, claimants = owners.setdefault(key, (alias, []))
            if unit.id not in claimants:
                claimants.append(unit.id)
    for _, (written, claimants) in sorted(owners.items()):
        if len(claimants) > 1:
            scan.diagnose(
                DiagnosticCode.AMBIGUOUS_ALIAS,
                f"{written} is a name of {' and '.join(sorted(claimants))}",
                *sorted(claimants),
            )
