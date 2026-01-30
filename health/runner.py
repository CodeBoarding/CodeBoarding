import logging
import os
from datetime import datetime, timezone
from pathlib import Path

from health.checks.circular_deps import check_circular_dependencies
from health.checks.cohesion import check_component_cohesion
from health.checks.coupling import check_fan_in, check_fan_out, collect_coupling_values
from health.checks.function_size import check_function_size, collect_function_sizes
from health.checks.god_class import check_god_classes, collect_god_class_values
from health.checks.inheritance import check_inheritance_depth, collect_inheritance_depths
from health.checks.instability import check_package_instability
from health.checks.orphan_code import check_orphan_code
from health.models import (
    CircularDependencyCheck,
    CodebaseStats,
    DistributionStats,
    FileHealthSummary,
    HealthCheckConfig,
    HealthReport,
    Severity,
    StandardCheckSummary,
)
from static_analyzer.analysis_result import StaticAnalysisResults
from static_analyzer.graph import CallGraph

logger = logging.getLogger(__name__)


def _compute_codebase_stats(call_graph: CallGraph, hierarchy: dict | None) -> CodebaseStats:
    """Compute statistical metrics from the codebase for adaptive thresholds.

    Delegates value collection to each check module and uses DistributionStats
    for uniform statistical computation.
    """
    function_sizes = collect_function_sizes(call_graph)
    fan_out_values, fan_in_values = collect_coupling_values(call_graph)
    method_counts, class_loc_values, class_fan_out_values = collect_god_class_values(call_graph)

    inheritance_depths: list[float] = []
    if hierarchy:
        inheritance_depths = collect_inheritance_depths(hierarchy)

    stats = CodebaseStats(
        function_size=DistributionStats.from_values(function_sizes),
        fan_out=DistributionStats.from_values(fan_out_values),
        fan_in=DistributionStats.from_values(fan_in_values),
        class_method_count=DistributionStats.from_values(method_counts),
        class_loc=DistributionStats.from_values(class_loc_values),
        class_fan_out=DistributionStats.from_values(class_fan_out_values),
        inheritance_depth=DistributionStats.from_values(inheritance_depths),
    )

    logger.info(
        "Codebase stats computed: function_size(mean=%s, p99=%s), fan_out(mean=%s, p99=%s), fan_in(mean=%s, p99=%s)",
        stats.function_size.mean if stats.function_size else None,
        stats.function_size.p99 if stats.function_size else None,
        stats.fan_out.mean if stats.fan_out else None,
        stats.fan_out.p99 if stats.fan_out else None,
        stats.fan_in.mean if stats.fan_in else None,
        stats.fan_in.p99 if stats.fan_in else None,
    )

    return stats


def _relativize_path(file_path: str, repo_root: str) -> str:
    """Convert an absolute file path to a path relative to the repository root."""
    return os.path.relpath(file_path, repo_root)


def run_health_checks(
    static_analysis: StaticAnalysisResults,
    repo_name: str,
    config: HealthCheckConfig | None = None,
    repo_path: Path | str | None = None,
) -> HealthReport:
    """Run all health checks against the static analysis results and produce a HealthReport.

    Args:
        static_analysis: The static analysis results to check.
        repo_name: Name of the repository.
        config: Optional health check configuration overrides.
        repo_path: Repository root path. When provided, all file paths in the
            report are made relative to this directory for portability.
    """
    if config is None:
        config = HealthCheckConfig()

    repo_root = str(repo_path) if repo_path is not None else None

    check_summaries: list[StandardCheckSummary | CircularDependencyCheck] = []

    for language in static_analysis.get_languages():
        call_graph = static_analysis.get_cfg(language)
        try:
            hierarchy = static_analysis.get_hierarchy(language)
        except ValueError:
            hierarchy = None

        config.codebase_stats = _compute_codebase_stats(call_graph, hierarchy)

        check_summaries.append(check_function_size(call_graph, config))
        check_summaries.append(check_fan_out(call_graph, config))
        check_summaries.append(check_fan_in(call_graph, config))
        check_summaries.append(check_god_classes(call_graph, hierarchy, config))

        if hierarchy:
            check_summaries.append(check_inheritance_depth(hierarchy, config))

        try:
            package_deps = static_analysis.get_package_dependencies(language)
        except ValueError:
            package_deps = None
        if package_deps:
            check_summaries.append(check_circular_dependencies(package_deps, config))
            check_summaries.append(check_package_instability(package_deps, config))

        check_summaries.append(check_component_cohesion(call_graph, config))
        check_summaries.append(check_orphan_code(call_graph))

    # Calculate overall score as weighted average
    total_entities = sum(s.total_entities_checked for s in check_summaries if isinstance(s, StandardCheckSummary))
    if total_entities > 0:
        overall_score = (
            sum(s.score * s.total_entities_checked for s in check_summaries if isinstance(s, StandardCheckSummary))
            / total_entities
        )
    else:
        overall_score = 1.0

    # Aggregate file-level summaries
    file_risk: dict[str, FileHealthSummary] = {}
    for summary in check_summaries:
        if isinstance(summary, StandardCheckSummary):
            for group in summary.finding_groups:
                for entity in group.entities:
                    if entity.file_path:
                        if entity.file_path not in file_risk:
                            file_risk[entity.file_path] = FileHealthSummary(file_path=entity.file_path)
                        file_risk[entity.file_path].total_findings += 1
                        if group.severity == Severity.WARNING:
                            file_risk[entity.file_path].warning_findings += 1

    for file_summary in file_risk.values():
        base_score = min(file_summary.total_findings * 10, 50)
        severity_bonus = file_summary.warning_findings * 5
        file_summary.composite_risk_score = min(base_score + severity_bonus, 100)

    file_summaries = sorted(file_risk.values(), key=lambda f: f.composite_risk_score, reverse=True)[:20]

    # Relativize paths if repo_root is provided
    if repo_root:
        for summary in check_summaries:
            if isinstance(summary, StandardCheckSummary):
                for group in summary.finding_groups:
                    for entity in group.entities:
                        if entity.file_path and os.path.isabs(entity.file_path):
                            entity.file_path = _relativize_path(entity.file_path, repo_root)
        for file_summary in file_summaries:
            if file_summary.file_path and os.path.isabs(file_summary.file_path):
                file_summary.file_path = _relativize_path(file_summary.file_path, repo_root)

    return HealthReport(
        repository_name=repo_name,
        timestamp=datetime.now(timezone.utc).isoformat(),
        overall_score=overall_score,
        check_summaries=check_summaries,
        file_summaries=file_summaries,
    )
