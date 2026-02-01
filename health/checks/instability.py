import logging

from health.models import FindingEntity, FindingGroup, HealthCheckConfig, Severity, StandardCheckSummary

logger = logging.getLogger(__name__)


def check_package_instability(package_dependencies: dict, config: HealthCheckConfig) -> StandardCheckSummary:
    """E9: Compute Martin's instability metric for each package.

    Instability I = Ce / (Ca + Ce) where:
    - Ce = efferent coupling (number of packages this package depends on)
    - Ca = afferent coupling (number of packages that depend on this package)

    I = 0.0 means maximally stable (heavily depended upon).
    I = 1.0 means maximally unstable (depends on others, nothing depends on it).

    Both extremes are flagged — highly unstable packages that are depended upon
    violate the Stable Dependencies Principle.
    """
    warning_entities: list[FindingEntity] = []
    total_checked = 0

    for package, info in package_dependencies.items():
        imports = info.get("imports", [])
        if isinstance(imports, dict):
            imports = list(imports.keys())
        imported_by = info.get("imported_by", [])
        if isinstance(imported_by, dict):
            imported_by = list(imported_by.keys())

        ce = len(imports)
        ca = len(imported_by)
        total_coupling = ca + ce

        if total_coupling == 0:
            continue

        total_checked += 1
        instability = ce / total_coupling

        if instability >= config.instability_high and ca > 0:
            warning_entities.append(
                FindingEntity(
                    entity_name=package,
                    file_path=None,
                    line_start=None,
                    line_end=None,
                    metric_value=round(instability, 3),
                )
            )

    finding_groups: list[FindingGroup] = []
    if warning_entities:
        finding_groups.append(
            FindingGroup(
                severity=Severity.WARNING,
                threshold=config.instability_high,
                description=f"Packages with instability >= {config.instability_high} that are depended on by others",
                entities=sorted(warning_entities, key=lambda e: e.metric_value, reverse=True),
            )
        )

    passing = total_checked - len(warning_entities)
    score = passing / total_checked if total_checked > 0 else 1.0

    return StandardCheckSummary(
        check_name="package_instability",
        description="Computes Martin's instability metric (I = Ce / (Ca + Ce)) per package",
        total_entities_checked=total_checked,
        findings_count=len(warning_entities),
        warning_count=len(warning_entities),
        score=score,
        finding_groups=finding_groups,
    )
